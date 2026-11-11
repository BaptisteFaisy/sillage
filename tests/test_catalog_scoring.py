"""Scores: success rate, freshness, token budget and dashboard figures.

The dashboard is tested against a local fake of the catalogue. The real
``catalog/local.py`` does not exist yet - it is written after this module - so
the fake lives here, in this file, and implements **only** the four methods
``catalogue_metrics`` calls, with the exact signatures of
``kernel.protocols.CatalogStore``. Nothing else is faked on purpose: a fake
that implemented the whole port would hide a missing dependency instead of
pinning one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sillage.catalog.scoring import (
    NEUTRAL_RATE,
    catalogue_metrics,
    estimate_tokens,
    freshness_days,
    is_stale,
    served,
    success_rate,
)
from sillage.kernel.types import (
    LEVEL_CODE,
    Client,
    Episode,
    Feedback,
    FeedbackResult,
    Fiche,
    Level,
    Scope,
    ScopeKind,
    Source,
)

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
AUTHORS = ("p-7f3a", "p-2b19")


class MiniCatalog:
    """Local fake of the catalogue methods the metrics read.

    Signatures are copied from ``kernel.protocols.CatalogStore`` so the metrics
    cannot drift from the port: if the port changes, this fake changes too.
    """

    def __init__(
        self,
        *,
        episodes: Sequence[Episode] = (),
        fiches: Sequence[Fiche] = (),
        clients: Sequence[Client] = (),
        feedbacks: dict[str, Sequence[Feedback]] | None = None,
    ) -> None:
        self._episodes = list(episodes)
        self._fiches = list(fiches)
        self._clients = list(clients)
        self._feedbacks = dict(feedbacks or {})

    def list_episodes(self, *, scope: Scope | None = None, limit: int = 50) -> Sequence[Episode]:
        return list(self._episodes[:limit])

    def list_fiches(self, *, scope: Scope | None = None, level: Level | None = None) -> Sequence[Fiche]:
        return list(self._fiches)

    def list_clients(self) -> Sequence[Client]:
        return list(self._clients)

    def list_feedback(self, fiche_id: str) -> Sequence[Feedback]:
        return list(self._feedbacks.get(fiche_id, ()))


def make_fiche(**changes: object) -> Fiche:
    """A fiche of client a, validated and confirmed."""
    base = Fiche(
        fiche_id="f-connexions",
        scope=Scope(kind=ScopeKind.CLIENT, client_id="a"),
        title="Connexions a la base epuisees",
        body="Le deploiement de 13h50 a porte le pool de 10 a 50 connexions par instance.",
        steps=("Comparer le pool configure et le pool reel.", "Revenir a 10 par instance."),
        level=Level.VALIDATED,
        success_count=5,
        distinct_authors=AUTHORS,
        last_confirmation=NOW - timedelta(days=10),
    )
    return replace(base, **changes)


class TestSuccessRate:
    def test_five_successes_without_failure(self) -> None:
        assert success_rate(successes=5, failures=0) == 6 / 7

    def test_no_feedback_at_all_is_neutral(self) -> None:
        assert success_rate(0, 0) == 0.5
        assert success_rate() == NEUTRAL_RATE

    def test_one_success_one_failure(self) -> None:
        assert success_rate(1, 1) == 0.5

    def test_two_successes_one_failure(self) -> None:
        assert success_rate(2, 1) == 3 / 5

    def test_only_failures(self) -> None:
        assert success_rate(0, 2) == 1 / 4

    def test_negative_counts_are_ignored(self) -> None:
        assert success_rate(-3, -4) == NEUTRAL_RATE

    def test_a_usage_without_feedback_does_not_enter_the_rate(self) -> None:
        """EF-RET-05: the rate is built from the feedbacks only."""
        fiche = make_fiche(success_count=2, failure_count=1, no_feedback_count=7)
        assert success_rate(successes=fiche.success_count, failures=fiche.failure_count) == 3 / 5


class TestFreshness:
    def test_days_since_the_last_confirmation(self) -> None:
        fiche = make_fiche(last_confirmation=NOW - timedelta(days=42))
        assert freshness_days(fiche, NOW) == 42

    def test_a_fiche_never_confirmed_has_no_freshness(self) -> None:
        assert freshness_days(make_fiche(last_confirmation=None), NOW) is None

    def test_a_confirmation_in_the_future_reads_as_zero(self) -> None:
        fiche = make_fiche(last_confirmation=NOW + timedelta(days=1))
        assert freshness_days(fiche, NOW) == 0

    def test_naive_dates_are_read_as_utc(self) -> None:
        naive = datetime(2026, 11, 1, 12, 0)
        assert freshness_days(make_fiche(last_confirmation=naive), NOW) == 10


class TestStale:
    def test_a_recent_fiche_is_not_stale(self) -> None:
        fiche = make_fiche(last_confirmation=NOW - timedelta(days=179))
        assert is_stale(fiche, NOW) is False

    def test_a_fiche_without_confirmation_for_180_days_is_stale(self) -> None:
        fiche = make_fiche(last_confirmation=NOW - timedelta(days=180))
        assert is_stale(fiche, NOW) is True

    def test_a_fiche_never_confirmed_is_not_stale(self) -> None:
        assert is_stale(make_fiche(last_confirmation=None), NOW) is False

    def test_the_threshold_is_a_setting(self) -> None:
        from sillage.kernel.config import Settings

        fiche = make_fiche(last_confirmation=NOW - timedelta(days=30))
        assert is_stale(fiche, NOW, settings=Settings(stale_days=20)) is True


class TestServed:
    def test_a_served_fiche_carries_its_rate_and_its_freshness(self) -> None:
        result = served(make_fiche(), NOW)
        assert result.success_rate == 6 / 7
        assert result.freshness_days == 10
        assert result.level_code == LEVEL_CODE[Level.VALIDATED]

    def test_the_generalisation_confidence_comes_from_the_memory(self) -> None:
        """It is provided with the lesson, never computed here (13.5)."""
        assert served(make_fiche(), NOW).generalisation_confidence is None


class TestEstimateTokens:
    def test_an_empty_text_costs_nothing(self) -> None:
        assert estimate_tokens("") == 0

    def test_the_estimate_grows_with_the_text(self) -> None:
        short = estimate_tokens("a" * 100)
        long = estimate_tokens("a" * 1000)
        assert 0 < short < long

    def test_a_three_fiche_answer_stays_under_the_budget(self) -> None:
        """ENF-PERF-03: the answer of a recall holds in 1500 tokens at most."""
        answer = "\n\n".join(render(make_fiche()) for _ in range(3))
        assert estimate_tokens(answer) < 1500


def render(fiche: Fiche) -> str:
    """Render a fiche the way the answer of a recall shows it."""
    lines = [f"[{LEVEL_CODE[fiche.level]}] {fiche.title}", fiche.body]
    lines += [f"- {step}" for step in fiche.steps]
    lines.append(f"Sources : {', '.join(fiche.sources) or 'aucune'}")
    return "\n".join(lines)


class TestCatalogueMetrics:
    def make_catalog(self) -> MiniCatalog:
        episodes = [
            Episode(
                episode_id=f"ep-{index}",
                scope=Scope(kind=ScopeKind.CLIENT, client_id="a"),
                work_item_id=f"guichet:ticket-{index}",
                source=Source.GUICHET,
            )
            for index in range(4)
        ]
        fiches = [
            make_fiche(fiche_id="f-1", success_count=5, failure_count=0),
            make_fiche(fiche_id="f-2", success_count=1, failure_count=1, last_confirmation=None),
            make_fiche(fiche_id="f-3", level=Level.PROVEN, success_count=4, failure_count=0),
            make_fiche(
                fiche_id="f-4",
                level=Level.SET_ASIDE,
                success_count=0,
                failure_count=2,
                consecutive_failures=2,
                last_confirmation=NOW - timedelta(days=200),
            ),
            make_fiche(
                fiche_id="f-5",
                level=Level.PROPOSED,
                success_count=0,
                failure_count=0,
                no_feedback_count=3,
                last_confirmation=None,
            ),
        ]
        clients = [Client(client_id="a", name="Client A"), Client(client_id="b", name="Client B")]
        feedbacks = {
            "f-1": [
                Feedback(usage_id="u-1", result=FeedbackResult.HELPED, fiche_id="f-1"),
                Feedback(usage_id="u-2", result=FeedbackResult.HELPED, fiche_id="f-1"),
            ],
            "f-2": [Feedback(usage_id="u-3", result=FeedbackResult.DID_NOT_HELP, fiche_id="f-2")],
        }
        return MiniCatalog(episodes=episodes, fiches=fiches, clients=clients, feedbacks=feedbacks)

    def test_episodes_and_fiches_are_counted(self) -> None:
        metrics = catalogue_metrics(self.make_catalog(), NOW)
        assert metrics["episodes"] == 4
        assert metrics["fiches"] == 5
        assert metrics["clients"] == 2

    def test_fiches_are_counted_per_level(self) -> None:
        metrics = catalogue_metrics(self.make_catalog(), NOW)
        assert metrics["fiches_by_level"] == {"0": 0, "1": 1, "2": 2, "3": 1, "E": 1, "R": 0}

    def test_reuse_is_reported(self) -> None:
        metrics = catalogue_metrics(self.make_catalog(), NOW)
        assert metrics["used_fiches"] == 5
        assert metrics["reuse_rate"] == 1.0

    def test_the_average_rate_covers_only_the_fiches_with_feedback(self) -> None:
        """f-5 has no feedback at all: it is counted apart, never averaged in."""
        metrics = catalogue_metrics(self.make_catalog(), NOW)
        assert metrics["success_rate"] == round((6 / 7 + 0.5 + 5 / 6 + 0.25) / 4, 4)

    def test_freshness_is_reported(self) -> None:
        metrics = catalogue_metrics(self.make_catalog(), NOW)
        assert metrics["freshness_days"] == round((10 + 10 + 200) / 3, 4)
        assert metrics["stale_fiches"] == 1

    def test_usages_without_feedback_are_counted_apart(self) -> None:
        metrics = catalogue_metrics(self.make_catalog(), NOW)
        assert metrics["usages_without_feedback"] == 3

    def test_no_figure_is_about_a_person(self) -> None:
        """EF-PIL-04 and ENF-RH-01: no author, no pseudonym, nowhere."""
        metrics = catalogue_metrics(self.make_catalog(), NOW)
        flat = repr(metrics)
        for author in AUTHORS:
            assert author not in flat
        for key in metrics:
            assert "author" not in key.lower()
            assert "auteur" not in key.lower()

    def test_an_empty_catalogue_gives_empty_figures(self) -> None:
        metrics = catalogue_metrics(MiniCatalog(), NOW)
        assert metrics["episodes"] == 0
        assert metrics["fiches"] == 0
        assert metrics["reuse_rate"] == 0.0
        assert metrics["success_rate"] is None
        assert metrics["freshness_days"] is None
