"""Cascade deletion and the full knowledge cycle of the catalogue.

``delete_scope`` is the requirement a client reads first (EF-ENT-03, specification
17.2): forget a perimeter completely, and report the fiches elsewhere that lose
their sources instead of deleting them silently. The second half of the file walks
the whole loop - episode, lesson, fiche, usage, feedback, promotion - because that
is what the demo shows on stage.
"""

from __future__ import annotations

import pytest

from catalog_samples import COMMON, NOW, make_episode, make_fiche, make_lesson, make_trace
from sillage.catalog.levels import apply_feedback
from sillage.catalog.local import LocalCatalog
from sillage.catalog.versions import bump
from sillage.kernel.types import Feedback, FeedbackResult, Level, Usage


@pytest.fixture
def catalog(clock) -> LocalCatalog:
    return LocalCatalog(clock)


class TestCascade:
    def test_a_cascade_deletes_every_row_of_the_perimeter(self, catalog: LocalCatalog, scope_a) -> None:
        catalog.put_trace(make_trace(scope_a))
        catalog.put_episode(make_episode(scope_a))
        catalog.put_lesson(make_lesson(scope_a))
        catalog.put_fiche(make_fiche(scope_a))
        catalog.put_usage(Usage("u-1", served_ids=("f-1",), scope=scope_a, at=NOW))
        catalog.put_feedback(Feedback("u-1", FeedbackResult.HELPED, at=NOW, fiche_id="f-1"))
        report = catalog.delete_scope(scope_a)
        assert report["travaux"] == 1
        assert report["episodes"] == 1
        assert report["lecons"] == 1
        assert report["fiches"] == 1
        assert report["usages"] == 1
        assert report["retours"] == 1
        assert catalog.get_trace("guichet:2041") is None
        assert catalog.get_episode("ep-1") is None
        assert catalog.get_fiche("f-1") is None
        assert catalog.list_feedback("f-1") == ()

    def test_the_other_perimeter_is_untouched(self, catalog: LocalCatalog, scope_a, scope_b) -> None:
        catalog.put_episode(make_episode(scope_a, "ep-a"))
        catalog.put_episode(make_episode(scope_b, "ep-b"))
        catalog.delete_scope(scope_a)
        assert [row.episode_id for row in catalog.list_episodes(scope=scope_b)] == ["ep-b"]

    def test_every_version_of_a_fiche_is_deleted(self, catalog: LocalCatalog, scope_a) -> None:
        fiche = make_fiche(scope_a)
        catalog.put_fiche(fiche)
        catalog.put_fiche(bump(fiche, body="correction"))
        # the report counts fiches, not rows: a fiche is one thing, its versions
        # are its history, and both rows leave with the perimeter
        assert catalog.delete_scope(scope_a)["fiches"] == 1
        assert catalog.get_fiche("f-1", 1) is None
        assert catalog.get_fiche("f-1", 2) is None

    def test_a_fiche_of_another_perimeter_with_a_dead_source_is_reported(
        self, catalog: LocalCatalog, scope_a
    ) -> None:
        catalog.put_trace(make_trace(scope_a))
        catalog.put_fiche(make_fiche(COMMON, "f-commun", sources=("guichet:2041",)))
        report = catalog.delete_scope(scope_a)
        assert report["fiches_signalees"] == 1
        assert [row.fiche_id for row in catalog.flagged_fiches()] == ["f-commun"]
        assert catalog.get_fiche("f-commun") is not None

    def test_a_lesson_of_another_perimeter_built_on_a_dead_episode_leaves(self, catalog, scope_a) -> None:
        catalog.put_episode(make_episode(scope_a, "ep-a"))
        catalog.put_lesson(make_lesson(COMMON, "le-commun", episode_ids=("ep-a",)))
        report = catalog.delete_scope(scope_a)
        assert report["lecons"] == 1
        assert catalog.get_lesson("le-commun") is None

    def test_a_usage_that_served_a_deleted_fiche_leaves_with_it(self, catalog, scope_a, scope_b) -> None:
        catalog.put_fiche(make_fiche(scope_a))
        catalog.put_usage(Usage("u-1", served_ids=("f-1",), scope=scope_b, at=NOW))
        assert catalog.delete_scope(scope_a)["usages"] == 1
        assert catalog.get_usage("u-1") is None

    def test_a_deleted_work_item_can_be_captured_again(self, catalog: LocalCatalog, scope_a) -> None:
        catalog.put_trace(make_trace(scope_a))
        catalog.mark_trace_seen("abc123", "guichet:2041")
        catalog.delete_scope(scope_a)
        assert catalog.trace_seen("abc123") is False

    def test_an_empty_perimeter_reports_only_zeros(self, catalog: LocalCatalog, scope_a) -> None:
        report = catalog.delete_scope(scope_a)
        assert set(report) == {
            "travaux",
            "episodes",
            "lecons",
            "fiches",
            "usages",
            "retours",
            "fiches_signalees",
        }
        assert sum(report.values()) == 0


class TestFullCycle:
    def test_episode_lesson_fiche_usage_feedback_and_promotion(self, catalog: LocalCatalog, scope_a) -> None:
        catalog.put_trace(make_trace(scope_a))
        catalog.put_episode(make_episode(scope_a))
        catalog.put_lesson(make_lesson(scope_a))
        fiche = make_fiche(scope_a)
        catalog.put_fiche(fiche)
        catalog.put_usage(Usage("u-1", served_ids=("f-1",), scope=scope_a, at=NOW))
        catalog.put_feedback(Feedback("u-1", FeedbackResult.HELPED, at=NOW, fiche_id="f-1"))
        confirmed = apply_feedback(fiche, FeedbackResult.HELPED, now=NOW, author="p-1")
        catalog.put_fiche(confirmed)
        assert catalog.get_fiche("f-1").success_count == 1
        assert catalog.get_fiche("f-1").last_confirmation == NOW
        assert len(catalog.list_feedback("f-1")) == 1

    def test_a_fiche_set_aside_leaves_the_validated_level(self, catalog: LocalCatalog, scope_a) -> None:
        fiche = make_fiche(scope_a)
        catalog.put_fiche(fiche)
        twice = apply_feedback(apply_feedback(fiche, FeedbackResult.WRONG), FeedbackResult.DID_NOT_HELP)
        catalog.put_fiche(twice)
        assert catalog.get_fiche("f-1").level is Level.SET_ASIDE
        assert catalog.list_fiches(level=Level.VALIDATED) == ()

    def test_a_usage_without_feedback_stays_out_of_the_rate(self, catalog: LocalCatalog, scope_a) -> None:
        fiche = make_fiche(scope_a)
        catalog.put_fiche(fiche)
        catalog.put_usage(Usage("u-9", served_ids=("f-1",), scope=scope_a, at=NOW))
        assert catalog.list_feedback("f-1") == ()
        assert catalog.get_fiche("f-1").success_count == 0

    def test_an_autonomous_agent_reads_only_validated_or_proven(self, catalog, scope_a) -> None:
        catalog.put_fiche(make_fiche(scope_a, "f-v", level=Level.VALIDATED))
        catalog.put_fiche(make_fiche(scope_a, "f-p", level=Level.PROPOSED))
        served = [row for row in catalog.list_fiches(scope=scope_a) if row.level is Level.VALIDATED]
        assert [row.fiche_id for row in served] == ["f-v"]

    def test_no_method_returns_a_per_person_figure(self, catalog: LocalCatalog) -> None:
        offenders = [name for name in dir(catalog) if "author" in name or "person" in name]
        assert offenders == []

    def test_a_promotion_is_written_as_a_new_version(self, catalog: LocalCatalog, scope_a) -> None:
        fiche = make_fiche(scope_a, level=Level.VALIDATED)
        catalog.put_fiche(fiche)
        catalog.put_fiche(bump(fiche, level=Level.PROVEN, success_count=3))
        assert catalog.get_fiche("f-1").level is Level.PROVEN
        assert catalog.get_fiche("f-1", 1).level is Level.VALIDATED
