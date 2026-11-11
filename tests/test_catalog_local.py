"""The offline catalogue: every port, every family, and the lexical search.

The reference behaviour of ``CatalogStore``: the demo, the portal, the tools and
the guards all read the catalogue through these methods, so each one is exercised
here. The cascade deletion and the full episode -> lesson -> fiche -> usage cycle
have their own file, ``test_catalog_cascade.py``, because a referent reads them
separately.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from catalog_samples import NOW, make_episode, make_fiche, make_lesson, make_trace
from sillage.catalog.local import ListJournal, ListNotifier, LocalBlobStore, LocalCatalog
from sillage.catalog.local_search import STOP_WORDS, query_words
from sillage.catalog.versions import bump, history
from sillage.kernel.protocols import BlobStore, CatalogStore, Journal, Notifier
from sillage.kernel.types import (
    Decision,
    Feedback,
    FeedbackResult,
    JournalEntry,
    Level,
    PrincipalRole,
    Usage,
)


def _entry(
    entry_id: str,
    *,
    tool: str = "recall",
    decision: Decision = Decision.PERMIT,
    at: datetime = NOW,
) -> JournalEntry:
    """A journal entry as the policy engine and the tools write them."""
    return JournalEntry(
        entry_id=entry_id,
        at=at,
        caller="p-7f3a",
        caller_role=PrincipalRole.ENGINEER,
        tool=tool,
        decision=decision,
        reason="test",
        scope="client-a",
    )


@pytest.fixture
def catalog(clock) -> LocalCatalog:
    return LocalCatalog(clock)


class TestPorts:
    def test_the_catalogue_is_every_port_it_promises(self, catalog: LocalCatalog) -> None:
        assert isinstance(catalog, CatalogStore)
        assert isinstance(catalog, BlobStore)
        assert isinstance(catalog, Journal)
        assert isinstance(catalog, Notifier)

    def test_a_blob_is_written_read_and_deleted(self, catalog: LocalCatalog) -> None:
        key = catalog.put("traces/t-1.json", '{"a": 1}')
        assert key == "traces/t-1.json"
        assert catalog.get(key) == '{"a": 1}'
        assert catalog.delete(key) == 1
        assert catalog.get(key) is None

    def test_a_blob_keeps_its_versions_and_delete_counts_them(self) -> None:
        blobs = LocalBlobStore()
        blobs.put("k", "v1")
        blobs.put("k", "v2")
        assert blobs.get("k") == "v2"
        assert blobs.versions_of("k") == 2
        assert blobs.delete("k") == 2
        assert blobs.keys() == ()

    def test_deleting_an_unknown_blob_removes_nothing(self) -> None:
        assert LocalBlobStore().delete("inconnu") == 0

    def test_the_journal_only_appends(self) -> None:
        journal = ListJournal()
        forbidden = {"update", "delete", "remove", "pop", "replace", "clear", "set", "edit"}
        assert forbidden.isdisjoint(set(dir(journal)))
        journal.append(_entry("j-1", tool="recall"))
        assert len(journal) == 1

    def test_the_journal_answers_the_newest_first_and_filters(self) -> None:
        journal = ListJournal()
        journal.append(_entry("j-1", tool="recall", at=NOW - timedelta(hours=2)))
        journal.append(_entry("j-2", tool="forget", decision=Decision.FORBID, at=NOW))
        assert [row.entry_id for row in journal.query()] == ["j-2", "j-1"]
        assert [row.entry_id for row in journal.query(tool="recall")] == ["j-1"]
        assert [row.entry_id for row in journal.query(decision=Decision.FORBID)] == ["j-2"]
        assert journal.query(limit=1)[0].entry_id == "j-2"

    def test_the_catalogue_journalises_through_the_same_port(self, catalog: LocalCatalog) -> None:
        catalog.append(_entry("j-3", tool="recall"))
        assert [row.entry_id for row in catalog.query()] == ["j-3"]
        assert len(catalog.journal) == 1

    def test_a_notification_is_recorded_for_its_referent(self, catalog: LocalCatalog) -> None:
        catalog.notify(referent="p-referent", subject="Fiche a valider", body="f-1", fiche_id="f-1")
        assert len(catalog.notifications()) == 1
        assert catalog.notifier.for_referent("p-referent")[0].fiche_id == "f-1"
        assert catalog.notifier.for_referent("p-autre") == ()

    def test_an_empty_notifier_has_no_message(self) -> None:
        assert len(ListNotifier()) == 0


class TestClients:
    def test_a_client_is_stored_and_read(self, catalog: LocalCatalog, clients) -> None:
        catalog.put_client(clients[0])
        assert catalog.get_client("a") == clients[0]
        assert catalog.get_client("inconnu") is None

    def test_clients_are_listed_in_a_stable_order(self, catalog: LocalCatalog, clients) -> None:
        catalog.put_client(clients[1])
        catalog.put_client(clients[0])
        assert [client.client_id for client in catalog.list_clients()] == ["a", "b"]

    def test_a_client_is_replaced_not_duplicated(self, catalog: LocalCatalog, clients) -> None:
        catalog.put_client(clients[0])
        catalog.put_client(replace(clients[0], name="Nouveau nom"))
        assert len(catalog.list_clients()) == 1
        assert catalog.get_client("a").name == "Nouveau nom"


class TestTraces:
    def test_a_trace_is_stored_and_read(self, catalog: LocalCatalog, scope_a) -> None:
        trace = make_trace(scope_a)
        catalog.put_trace(trace)
        assert catalog.get_trace("guichet:2041") == trace
        assert catalog.get_trace("inconnu") is None

    def test_a_fingerprint_remembers_its_work_item(self, catalog: LocalCatalog) -> None:
        assert catalog.trace_seen("abc") is False
        catalog.mark_trace_seen("abc", "guichet:2041")
        assert catalog.trace_seen("abc") is True

    def test_the_fixture_trace_is_stored_as_it_came(self, catalog: LocalCatalog, trace_ticket) -> None:
        catalog.put_trace(trace_ticket)
        assert catalog.get_trace(trace_ticket.work_item_id).events == trace_ticket.events


class TestEpisodes:
    def test_an_episode_is_stored_and_read(self, catalog: LocalCatalog, scope_a) -> None:
        episode = make_episode(scope_a)
        catalog.put_episode(episode)
        assert catalog.get_episode("ep-1") == episode
        assert catalog.get_episode("inconnu") is None

    def test_episodes_are_listed_perimeter_by_perimeter(
        self, catalog: LocalCatalog, scope_a, scope_b
    ) -> None:
        catalog.put_episode(make_episode(scope_a, "ep-a"))
        catalog.put_episode(make_episode(scope_b, "ep-b", work_item_id="guichet:9"))
        assert [row.episode_id for row in catalog.list_episodes(scope=scope_a)] == ["ep-a"]
        assert [row.episode_id for row in catalog.list_episodes(scope=scope_b)] == ["ep-b"]
        assert len(catalog.list_episodes()) == 2

    def test_the_newest_episode_comes_first(self, catalog: LocalCatalog, scope_a) -> None:
        catalog.put_episode(make_episode(scope_a, "ep-vieux", created_at=NOW - timedelta(days=3)))
        catalog.put_episode(make_episode(scope_a, "ep-neuf", created_at=NOW))
        assert [row.episode_id for row in catalog.list_episodes(scope=scope_a)] == ["ep-neuf", "ep-vieux"]

    def test_the_limit_is_applied(self, catalog: LocalCatalog, scope_a) -> None:
        for index in range(5):
            catalog.put_episode(make_episode(scope_a, f"ep-{index}", created_at=NOW + timedelta(hours=index)))
        assert len(catalog.list_episodes(scope=scope_a, limit=2)) == 2

    def test_an_episode_without_a_date_is_listed_last(self, catalog: LocalCatalog, scope_a) -> None:
        catalog.put_episode(make_episode(scope_a, "ep-sans-date", created_at=None))
        catalog.put_episode(make_episode(scope_a, "ep-date"))
        assert [row.episode_id for row in catalog.list_episodes(scope=scope_a)] == ["ep-date", "ep-sans-date"]


class TestLessons:
    def test_a_lesson_is_stored_and_read(self, catalog: LocalCatalog, scope_a) -> None:
        lesson = make_lesson(scope_a)
        catalog.put_lesson(lesson)
        assert catalog.get_lesson("le-1") == lesson
        assert catalog.get_lesson("inconnu") is None

    def test_lessons_are_listed_perimeter_by_perimeter(self, catalog: LocalCatalog, scope_a, scope_b) -> None:
        catalog.put_lesson(make_lesson(scope_a, "le-a"))
        catalog.put_lesson(make_lesson(scope_b, "le-b"))
        assert [row.lesson_id for row in catalog.list_lessons(scope=scope_a)] == ["le-a"]
        assert len(catalog.list_lessons()) == 2


class TestFichesAndVersions:
    def test_a_fiche_is_stored_and_read(self, catalog: LocalCatalog, scope_a) -> None:
        fiche = make_fiche(scope_a)
        catalog.put_fiche(fiche)
        assert catalog.get_fiche("f-1") == fiche
        assert catalog.get_fiche("inconnue") is None

    def test_the_latest_version_is_returned_by_default(self, catalog: LocalCatalog, scope_a) -> None:
        fiche = make_fiche(scope_a)
        catalog.put_fiche(fiche)
        catalog.put_fiche(bump(fiche, body="correction"))
        assert catalog.get_fiche("f-1").version == 2
        assert catalog.get_fiche("f-1", 1).body == fiche.body

    def test_versions_history_reads_the_kept_rows(self, catalog: LocalCatalog, scope_a) -> None:
        fiche = make_fiche(scope_a)
        for index in range(3):
            catalog.put_fiche(fiche)
            fiche = bump(fiche, title=f"Correction {index + 1}")
        kept = history(catalog, "f-1")
        assert [row.version for row in kept] == [1, 2, 3]
        assert kept[-1].title == "Correction 2"

    def test_an_unknown_version_is_absent(self, catalog: LocalCatalog, scope_a) -> None:
        catalog.put_fiche(make_fiche(scope_a))
        assert catalog.get_fiche("f-1", 7) is None

    def test_list_fiches_returns_the_latest_version_only(self, catalog: LocalCatalog, scope_a) -> None:
        fiche = make_fiche(scope_a)
        catalog.put_fiche(fiche)
        catalog.put_fiche(bump(fiche, body="correction"))
        rows = catalog.list_fiches(scope=scope_a)
        assert len(rows) == 1 and rows[0].version == 2

    def test_list_fiches_filters_the_perimeter_and_the_level(
        self, catalog: LocalCatalog, scope_a, scope_b
    ) -> None:
        catalog.put_fiche(make_fiche(scope_a, "f-a", level=Level.VALIDATED))
        catalog.put_fiche(make_fiche(scope_b, "f-b", level=Level.PROPOSED))
        assert [row.fiche_id for row in catalog.list_fiches(scope=scope_a)] == ["f-a"]
        assert [row.fiche_id for row in catalog.list_fiches(level=Level.PROPOSED)] == ["f-b"]
        assert len(catalog.list_fiches()) == 2

    def test_a_withdrawn_fiche_stays_readable(self, catalog: LocalCatalog, scope_a) -> None:
        catalog.put_fiche(make_fiche(scope_a, level=Level.WITHDRAWN))
        assert catalog.list_fiches(scope=scope_a)[0].level is Level.WITHDRAWN


class TestUsagesAndFeedback:
    def test_a_usage_is_stored_and_read(self, catalog: LocalCatalog, scope_a) -> None:
        usage = Usage(usage_id="u-1", served_ids=("f-1",), scope=scope_a, at=NOW, problem="500")
        catalog.put_usage(usage)
        assert catalog.get_usage("u-1") == usage
        assert catalog.get_usage("inconnu") is None

    def test_feedback_is_listed_per_fiche(self, catalog: LocalCatalog) -> None:
        catalog.put_feedback(Feedback("u-1", FeedbackResult.HELPED, at=NOW, fiche_id="f-1"))
        catalog.put_feedback(Feedback("u-2", FeedbackResult.WRONG, at=NOW, fiche_id="f-2"))
        assert [row.usage_id for row in catalog.list_feedback("f-1")] == ["u-1"]
        assert len(catalog.list_feedback("f-inconnue")) == 0

    def test_one_answer_per_usage_replaces_the_previous_one(self, catalog: LocalCatalog) -> None:
        catalog.put_feedback(Feedback("u-1", FeedbackResult.HELPED, at=NOW, fiche_id="f-1"))
        catalog.put_feedback(Feedback("u-1", FeedbackResult.WRONG, at=NOW, fiche_id="f-1"))
        rows = catalog.list_feedback("f-1")
        assert len(rows) == 1 and rows[0].result is FeedbackResult.WRONG

    def test_an_answer_without_a_date_sorts_as_the_oldest(self, catalog: LocalCatalog) -> None:
        catalog.put_feedback(Feedback("u-1", FeedbackResult.HELPED, at=NOW, fiche_id="f-1"))
        catalog.put_feedback(Feedback("u-2", FeedbackResult.WRONG, at=None, fiche_id="f-1"))
        assert [row.usage_id for row in catalog.list_feedback("f-1")] == ["u-2", "u-1"]


class TestSearch:
    def test_a_query_finds_the_fiche_of_its_perimeter(self, catalog: LocalCatalog, scope_a) -> None:
        catalog.put_fiche(make_fiche(scope_a))
        found = catalog.search_fiches(scope=scope_a, terms=["connexions", "pool"])
        assert [row.fiche_id for row in found] == ["f-1"]

    def test_the_search_never_crosses_a_perimeter(self, catalog: LocalCatalog, scope_a, scope_b) -> None:
        catalog.put_fiche(make_fiche(scope_b, "f-b"))
        assert catalog.search_fiches(scope=scope_a, terms=["connexions"]) == ()

    def test_accents_and_case_are_folded(self, catalog: LocalCatalog, scope_a) -> None:
        catalog.put_fiche(make_fiche(scope_a, title="Connexions à la base épuisées"))
        assert catalog.search_fiches(scope=scope_a, terms=["EPUISEES"])

    def test_a_stem_finds_its_plural(self, catalog: LocalCatalog, scope_a) -> None:
        catalog.put_fiche(make_fiche(scope_a))
        assert catalog.search_fiches(scope=scope_a, terms=["connexion"])

    def test_more_recognised_terms_rank_first(self, catalog: LocalCatalog, scope_a) -> None:
        catalog.put_fiche(make_fiche(scope_a, "f-deux", title="Pool de connexions", body="rien d'autre"))
        catalog.put_fiche(make_fiche(scope_a, "f-un", title="Veille technologique", body="rien d'autre"))
        found = catalog.search_fiches(scope=scope_a, terms=["pool", "connexions", "veille"])
        assert [row.fiche_id for row in found] == ["f-deux", "f-un"]

    def test_the_level_breaks_a_tie(self, catalog: LocalCatalog, scope_a) -> None:
        catalog.put_fiche(make_fiche(scope_a, "f-a", level=Level.VALIDATED))
        catalog.put_fiche(make_fiche(scope_a, "f-b", level=Level.PROVEN))
        found = catalog.search_fiches(scope=scope_a, terms=["connexions"])
        assert [row.fiche_id for row in found] == ["f-b", "f-a"]

    def test_the_steps_and_the_validity_are_searched(self, catalog: LocalCatalog, scope_a) -> None:
        catalog.put_fiche(make_fiche(scope_a))
        assert catalog.search_fiches(scope=scope_a, terms=["instance"])
        assert catalog.search_fiches(scope=scope_a, terms=["ecs"])

    def test_the_limit_is_applied(self, catalog: LocalCatalog, scope_a) -> None:
        for index in range(4):
            catalog.put_fiche(make_fiche(scope_a, f"f-{index}"))
        assert len(catalog.search_fiches(scope=scope_a, terms=["connexions"], limit=2)) == 2

    def test_a_query_of_stop_words_only_returns_nothing(self, catalog: LocalCatalog, scope_a) -> None:
        catalog.put_fiche(make_fiche(scope_a))
        assert catalog.search_fiches(scope=scope_a, terms=["le", "la", "de"]) == ()

    def test_the_query_normalisation_drops_stop_words_and_duplicates(self) -> None:
        assert query_words(["le", "Connexions", "connexions", "de"]) == ("connexions",)
        assert "le" in STOP_WORDS

    def test_a_withdrawn_fiche_is_still_found_for_a_human(self, catalog: LocalCatalog, scope_a) -> None:
        catalog.put_fiche(make_fiche(scope_a, level=Level.WITHDRAWN))
        assert catalog.search_fiches(scope=scope_a, terms=["connexions"])
