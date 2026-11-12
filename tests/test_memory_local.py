"""``LocalMemoryBackend``: the port, the batches, the search, the reflection.

Everything here runs offline, with the standard library alone. What is checked is
what the deployed service would refuse or answer differently:

* the 100 events per call limit, and the stable client token of each batch;
* a job marked "do not keep" is stored and never distilled (EF-CAP-12, R4);
* the search is a tf-idf cosine over the record contents - an intention finds the
  episodes that answer it, a raw error message does not (specification 13.4);
* three close episodes of one client give one lesson linked to the three (R5),
  and a reflection never mixes two perimeters (EF-DIS-02);
* the common space never leads back to the episode it was drawn from (R15).
"""

from __future__ import annotations

import dataclasses
import sys
from dataclasses import replace

import pytest

from memory_fakes import (
    CLOSED_WITHOUT_SUCCESS,
    MOMENT,
    client_space,
    connection_job,
    job_trace,
    many_step_trace,
    proxy_job,
    record,
    replay,
)
from sillage.capture import sample
from sillage.capture.ingest import default_memory
from sillage.capture.offline_memory import OfflineMemoryBackend
from sillage.capture.offline_memory import StoredEvent as FallbackEvent
from sillage.kernel.config import Settings
from sillage.kernel.ids import episode_id
from sillage.kernel.protocols import MemoryBackend
from sillage.kernel.types import ExtractionMode, Level, Scope, ScopeKind
from sillage.memory.local import LocalMemoryBackend
from sillage.memory.local_records import StoredEvent


@pytest.fixture
def backend(settings, clock) -> LocalMemoryBackend:
    return LocalMemoryBackend(settings, clock)


class TestThePort:
    """The backend satisfies the port the capture and the tools speak."""

    def test_it_satisfies_the_memory_port(self, backend) -> None:
        assert isinstance(backend, MemoryBackend)

    def test_the_smoke_builds_it_with_one_argument(self) -> None:
        """The smoke calls ``LocalMemoryBackend(Settings())`` and nothing more."""
        assert LocalMemoryBackend(Settings()).namespace("client-a", "ticket-2041")

    def test_it_ensures_a_memory_once(self, backend, settings) -> None:
        first = backend.ensure_memory(settings.memory_name, settings.memory_description)
        assert first and first == backend.ensure_memory("autre", "autre")

    def test_it_replaces_the_fallback_of_the_capture(self, settings) -> None:
        """The capture prefers this backend as soon as the module exists."""
        built = default_memory(settings)
        assert isinstance(built, LocalMemoryBackend)
        assert not isinstance(built, OfflineMemoryBackend)

    def test_it_needs_no_aws_and_no_network(self, backend, settings) -> None:
        replay(backend, connection_job(0))
        backend.retrieve(namespace=client_space(backend), query="connexions", top_k=3)
        for module in ("boto3", "botocore"):
            assert module not in sys.modules, f"{module} importe par la mémoire hors ligne"

    def test_the_stored_event_keeps_the_fields_of_the_fallback(self) -> None:
        """A test written against the fallback reads the same attributes here."""
        mine = [field.name for field in dataclasses.fields(StoredEvent)]
        theirs = [field.name for field in dataclasses.fields(FallbackEvent)]
        assert mine[: len(theirs)] == theirs


class TestTheNamespace:
    """The spaces are the templates of the kernel, and nothing else."""

    def test_the_namespace_of_a_job(self, backend, settings) -> None:
        assert backend.namespace("client-a", "ticket-2041") == settings.episode_namespace(
            "client-a", "ticket-2041"
        )

    def test_the_namespace_of_a_perimeter(self, backend, settings) -> None:
        assert backend.namespace("client-a") == settings.episode_namespace("client-a", "")

    def test_two_clients_never_share_a_namespace(self, backend) -> None:
        mine = backend.namespace("client-a", "ticket-2041")
        theirs = backend.namespace("client-b", "ticket-2041")
        assert mine != theirs
        assert not theirs.startswith(mine) and not mine.startswith(theirs)


class TestTheEvents:
    """A trace is replayed as dated events, in batches of 100 (annex B)."""

    def test_every_event_is_replayed(self, backend) -> None:
        trace = connection_job(0)
        replay(backend, trace)
        assert len(backend.events()) == len(trace.events)

    def test_the_roles_and_the_kinds_survive(self, backend) -> None:
        trace = sample.ticket_2041()
        replay(backend, trace)
        stored = backend.events(session_id=trace.session_id)
        assert [event.role for event in stored] == [event.role.value for event in trace.events]
        assert [event.kind for event in stored] == [event.kind for event in trace.events]

    def test_the_reference_ticket_is_replayed(self, backend) -> None:
        trace = sample.ticket_2041()
        assert replay(backend, trace)
        assert len(backend.events(session_id=trace.session_id)) == len(trace.events)

    def test_the_batch_limit_is_respected(self, backend, settings) -> None:
        limit = settings.max_events_per_call
        trace = many_step_trace(limit + 50)
        replay(backend, trace)
        stored = backend.events()
        expected = len(trace.events)
        assert len(stored) == expected == 2 * (limit + 50) + 2
        assert max(event.batch for event in stored) == -(-expected // limit)
        assert sum(1 for event in stored if event.batch == 1) == limit
        assert all(len([e for e in stored if e.batch == n]) <= limit for n in (1, 2, 3))

    def test_the_client_token_is_stable_per_batch(self, backend, settings) -> None:
        trace = many_step_trace(settings.max_events_per_call + 20)
        replay(backend, trace)
        tokens = sorted({event.client_token for event in backend.events()})
        assert tokens == [f"{trace.session_id}-part-{index}" for index in (1, 2, 3)]

    def test_the_same_trace_replayed_twice_gives_the_same_tokens(self, backend) -> None:
        trace = many_step_trace(60)
        replay(backend, trace)
        first = [event.client_token for event in backend.events()]
        replay(backend, trace)
        again = [event.client_token for event in backend.events()][len(first) :]
        assert again == first

    def test_a_trace_without_events_replays_nothing(self, backend) -> None:
        trace = replace(connection_job(0), events=())
        assert replay(backend, trace)
        assert backend.events() == ()
        assert backend.records() == ()


class TestTheDistillation:
    """EF-DIS-01 to EF-DIS-05: one episode per job, its failed paths kept apart."""

    def test_a_replayed_job_becomes_one_episode_record(self, backend) -> None:
        trace = connection_job(0)
        replay(backend, trace)
        namespace = backend.namespace(trace.scope.actor_id, trace.session_id)
        found = backend.list_records(namespace_path=namespace)
        assert len(found) == 1
        assert found[0].record_id == episode_id("client-a", trace.work_item_id)

    def test_the_episode_keeps_a_link_to_its_origin(self, backend) -> None:
        trace = connection_job(0)
        replay(backend, trace)
        found = backend.list_records(namespace_path=client_space(backend))
        assert found[0].metadata["workItemId"] == trace.work_item_id
        assert found[0].metadata["scope"] == "client-a"

    def test_the_episode_tells_its_verdict(self, backend) -> None:
        replay(backend, connection_job(0))
        found = backend.list_records(namespace_path=client_space(backend))
        assert found[0].metadata["outcome"] == "success"
        assert "Verdict : success" in found[0].content

    def test_a_failed_job_says_so(self, backend) -> None:
        trace = job_trace(
            work_item_id="guichet:ticket-9000",
            situation="La base refuse les connexions.",
            steps=(("Je redémarre la base.", "La base ne répond toujours pas."),),
            closing=CLOSED_WITHOUT_SUCCESS,
        )
        replay(backend, trace)
        found = backend.list_records(namespace_path=client_space(backend))
        assert found[0].metadata["outcome"] == "failure"

    def test_the_failed_paths_are_kept_apart(self, backend) -> None:
        """EF-DIS-04: the wrong lead stays in the episode, on its own line."""
        replay(backend, connection_job(0))
        content = backend.list_records(namespace_path=client_space(backend))[0].content
        assert "Pistes essayées sans succès :" in content
        assert "ne règle rien" in content.split("Pistes essayées sans succès :")[1]

    def test_the_teachings_are_kept_apart_too(self, backend) -> None:
        replay(backend, connection_job(0))
        content = backend.list_records(namespace_path=client_space(backend))[0].content
        assert "Enseignements :" in content
        assert "Déroulé :" in content

    def test_a_job_marked_do_not_keep_is_stored_without_being_distilled(self, backend) -> None:
        """R4 / EF-CAP-12: stored, never distilled, served to nobody."""
        trace = connection_job(1)
        replay(backend, trace, mode=ExtractionMode.SKIP)
        stored = backend.events(extraction_mode=ExtractionMode.SKIP)
        assert len(stored) == len(trace.events)
        assert backend.records() == ()
        assert backend.retrieve(namespace=client_space(backend), query="connexions base") == ()
        assert backend.reflections(actor_id="client-a") == ()

    def test_a_kept_job_is_replayed_with_the_default_mode(self, backend) -> None:
        trace = connection_job(2)
        replay(backend, trace)
        assert {event.extraction_mode for event in backend.events()} == {ExtractionMode.DEFAULT}


class TestTheSearch:
    """The offline semantic search: tf-idf and cosine over the record contents."""

    def test_the_smoke_finds_the_reference_ticket(self, backend) -> None:
        """The exact calls of the ``memoire`` probe of the smoke."""
        backend.ensure_memory(Settings().memory_name, "smoke")
        backend.create_events(actor_id="client-a", session_id="ticket-2041", trace=sample.ticket_2041())
        hits = backend.retrieve(
            namespace=backend.namespace("client-a", "ticket-2041"), query="connexions base", top_k=3
        )
        assert hits
        assert hits[0].score > 0

    def test_the_closest_record_comes_first(self, backend, settings) -> None:
        backend.put_records(
            [
                record(
                    record_id="r-base",
                    namespace="/commun/",
                    content="Rétablir les connexions de la base après un déploiement.",
                ),
                record(
                    record_id="r-certificat",
                    namespace="/commun/",
                    content="Renouveler un certificat expiré sur le mandataire.",
                ),
            ]
        )
        hits = backend.retrieve(namespace="/commun/", query="connexions base déploiement", top_k=2)
        assert [hit.record_id for hit in hits] == ["r-base", "r-certificat"][: len(hits)]
        assert hits[0].record_id == "r-base"

    def test_the_accents_are_folded(self, backend) -> None:
        backend.put_records(
            [record(record_id="r-1", namespace="/commun/", content="Connexions épuisées sur la base.")]
        )
        assert backend.retrieve(namespace="/commun/", query="connexions epuisees base")

    def test_the_punctuation_is_ignored(self, backend) -> None:
        backend.put_records(
            [record(record_id="r-1", namespace="/commun/", content="Trop de connexions : la base sature !")]
        )
        assert backend.retrieve(namespace="/commun/", query="connexions base ?")

    def test_the_stop_words_of_both_languages_are_ignored(self, backend) -> None:
        backend.put_records(
            [record(record_id="r-1", namespace="/commun/", content="Le groupe de connexions de la base.")]
        )
        with_noise = backend.retrieve(namespace="/commun/", query="le the and de connexions base")
        without = backend.retrieve(namespace="/commun/", query="connexions base")
        assert [hit.record_id for hit in with_noise] == [hit.record_id for hit in without]

    def test_the_search_is_deterministic(self, backend) -> None:
        for number in range(3):
            replay(backend, connection_job(number))
        first = backend.retrieve(namespace=client_space(backend), query="connexions base")
        second = backend.retrieve(namespace=client_space(backend), query="connexions base")
        assert [(hit.record_id, hit.score) for hit in first] == [(hit.record_id, hit.score) for hit in second]

    def test_the_search_stays_inside_the_namespace(self, backend) -> None:
        replay(backend, connection_job(0, client="a"))
        replay(backend, connection_job(0, client="b"))
        mine = backend.retrieve(namespace=client_space(backend, "b"), query="connexions base")
        assert mine
        assert all("client-b" in hit.namespace for hit in mine)

    def test_the_top_k_is_respected(self, backend) -> None:
        for number in range(5):
            replay(backend, connection_job(number))
        hits = backend.retrieve(namespace=client_space(backend), query="connexions base", top_k=2)
        assert len(hits) == 2

    def test_a_query_without_a_shared_word_finds_nothing(self, backend) -> None:
        replay(backend, connection_job(0))
        assert backend.retrieve(namespace=client_space(backend), query="imprimante réseau") == ()

    def test_an_empty_query_finds_nothing(self, backend) -> None:
        replay(backend, connection_job(0))
        assert backend.retrieve(namespace=client_space(backend), query="") == ()


class TestTheApprovedText:
    """The text a referent approved is written verbatim, and found again."""

    def test_the_text_is_written_verbatim(self, backend) -> None:
        text = "Rétablir le pool de connexions : 10 par instance, jamais 50.\nÀ ne pas faire : redémarrer."
        backend.put_records([record(record_id="f-1", namespace="/fiches/client-a/", content=text)])
        assert backend.record("f-1").content == text

    def test_the_approved_text_is_searchable(self, backend) -> None:
        backend.put_records(
            [
                record(
                    record_id="f-1",
                    namespace="/fiches/client-a/",
                    content="Rétablir le pool de connexions de la base après un déploiement.",
                )
            ]
        )
        hits = backend.retrieve(namespace="/fiches/client-a/", query="pool connexions base")
        assert [hit.record_id for hit in hits] == ["f-1"]

    def test_the_prefix_walk_finds_every_session_of_a_client(self, backend, settings) -> None:
        for number in range(3):
            replay(backend, connection_job(number))
        namespace = settings.reflection_namespace("client-a")
        found = backend.list_records(namespace_path=namespace)
        assert len(found) == 3
        assert all(record_.namespace.startswith(namespace) for record_ in found)

    def test_the_prefix_walk_stops_at_the_perimeter(self, backend, settings) -> None:
        replay(backend, connection_job(0, client="a"))
        replay(backend, connection_job(0, client="b"))
        found = backend.list_records(namespace_path=settings.reflection_namespace("client-a"))
        assert len(found) == 1
        assert "client-b" not in found[0].namespace

    def test_the_listing_is_ordered_by_date_then_id(self, backend) -> None:
        backend.put_records(
            [
                record(record_id="r-b", namespace="/commun/", content="b", created_at=MOMENT),
                record(record_id="r-a", namespace="/commun/", content="a", created_at=MOMENT),
            ]
        )
        assert [hit.record_id for hit in backend.list_records(namespace_path="/commun/")] == [
            "r-a",
            "r-b",
        ]


class TestTheForgetting:
    """EF-ENT-03, R19: a job can be forgotten, and only that job."""

    def test_a_forgotten_job_disappears(self, backend) -> None:
        trace = connection_job(0)
        replay(backend, trace)
        removed = backend.delete_records(actor_id="client-a", session_id=trace.session_id)
        assert removed == len(trace.events) + 1
        assert backend.events() == ()
        assert backend.records() == ()

    def test_a_forgotten_job_is_no_longer_searchable(self, backend) -> None:
        trace = connection_job(0)
        replay(backend, trace)
        backend.delete_records(actor_id="client-a", session_id=trace.session_id)
        assert backend.retrieve(namespace=client_space(backend), query="connexions base") == ()

    def test_an_unknown_job_forgets_nothing(self, backend) -> None:
        assert backend.delete_records(actor_id="client-a", session_id="inconnu") == 0

    def test_another_client_is_untouched(self, backend) -> None:
        replay(backend, connection_job(0, client="a"))
        trace_b = connection_job(0, client="b")
        replay(backend, trace_b)
        backend.delete_records(actor_id="client-b", session_id=trace_b.session_id)
        assert len(backend.records()) == 1
        assert "client-a" in backend.records()[0].namespace


@pytest.mark.recette
class TestR5ThreeCloseEpisodesGiveOneLesson:
    """R5: three close episodes of one client give one lesson, linked to the three."""

    def test_one_lesson_linked_to_the_three_episodes(self, backend) -> None:
        for number in range(3):
            replay(backend, connection_job(number))
        lessons = backend.reflections(actor_id="client-a")
        assert len(lessons) == 1
        assert len(lessons[0].episode_ids) == 3

    def test_the_lesson_carries_its_perimeter_and_its_level(self, backend) -> None:
        for number in range(3):
            replay(backend, connection_job(number))
        lesson = backend.reflections(actor_id="client-a")[0]
        assert lesson.scope == Scope(kind=ScopeKind.CLIENT, client_id="a")
        assert lesson.level is Level.PROPOSED

    def test_the_confidence_is_inside_its_range(self, backend) -> None:
        for number in range(3):
            replay(backend, connection_job(number))
        lesson = backend.reflections(actor_id="client-a")[0]
        assert 0.1 <= lesson.confidence <= 1.0

    def test_the_lesson_keeps_the_traps_apart(self, backend) -> None:
        for number in range(3):
            replay(backend, connection_job(number))
        lesson = backend.reflections(actor_id="client-a")[0]
        assert lesson.pitfalls
        assert any("ne règle rien" in pitfall for pitfall in lesson.pitfalls)

    def test_another_technological_family_stays_out(self, backend) -> None:
        for number in range(3):
            replay(backend, connection_job(number))
        replay(backend, proxy_job(1))
        lessons = backend.reflections(actor_id="client-a")
        assert len(lessons) == 1
        assert len(lessons[0].episode_ids) == 3

    def test_one_episode_alone_gives_no_lesson(self, backend) -> None:
        replay(backend, connection_job(0))
        assert backend.reflections(actor_id="client-a") == ()

    def test_the_reflection_is_deterministic(self, backend) -> None:
        for number in range(3):
            replay(backend, connection_job(number))
        first = backend.reflections(actor_id="client-a")
        second = backend.reflections(actor_id="client-a")
        assert [lesson.lesson_id for lesson in first] == [lesson.lesson_id for lesson in second]


class TestEF_DIS_02TheReflectionNeverMixesTwoPerimeters:
    """Non regression: a reflection is made of the episodes of one actor only."""

    def test_two_clients_with_the_same_trouble_give_two_lessons(self, backend) -> None:
        for number in range(3):
            replay(backend, connection_job(number, client="a"))
            replay(backend, connection_job(number, client="b"))
        mine = backend.reflections(actor_id="client-a")
        theirs = backend.reflections(actor_id="client-b")
        assert len(mine) == 1 and len(theirs) == 1
        assert mine[0].lesson_id != theirs[0].lesson_id

    def test_no_episode_of_another_client_enters_a_lesson(self, backend) -> None:
        for number in range(3):
            replay(backend, connection_job(number, client="a"))
            replay(backend, connection_job(number, client="b"))
        mine = backend.reflections(actor_id="client-a")[0]
        episodes_of_a = {
            record_.record_id for record_ in backend.list_records(namespace_path=client_space(backend))
        }
        assert set(mine.episode_ids) <= episodes_of_a
        assert mine.scope.actor_id == "client-a"

    def test_the_lessons_of_two_clients_never_share_an_episode(self, backend) -> None:
        for number in range(3):
            replay(backend, connection_job(number, client="a"))
            replay(backend, connection_job(number, client="b"))
        mine = backend.reflections(actor_id="client-a")[0]
        theirs = backend.reflections(actor_id="client-b")[0]
        assert not set(mine.episode_ids) & set(theirs.episode_ids)
