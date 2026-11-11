"""Ingestion: the pipeline, in the order the specification imposes.

Recipes R3 (the same trace twice gives one episode), R4 (a job marked "do not
keep" is stored and never distilled) and R11 (a rejected diagnosis stays in the
episode), plus EF-CAP-12, EF-CAP-13, the guardrail screen of chapter 10.3 and
the offline wiring the smoke relies on.

Everything here runs with zero dependency: the catalogue and the object store
are the offline ones of :mod:`sillage.catalog`, and the memory backend is the
capture's own recorder.
"""

from __future__ import annotations

import json
import sys
import types
from dataclasses import replace

import pytest

from sillage.capture import sample
from sillage.capture.ingest import IngestReport, IngestService, build_default_service, default_memory
from sillage.capture.offline_memory import OfflineMemoryBackend
from sillage.catalog.local import LocalBlobStore, LocalCatalog
from sillage.catalog.model import CAPTURE_EVENTS, CAPTURE_SKIPPED, CAPTURE_STORED
from sillage.kernel.errors import BackendUnavailable, NotFound, PoisonedContent
from sillage.kernel.ids import episode_id, pseudonym
from sillage.kernel.protocols import GuardrailVerdict, MemoryBackend, MemoryRecord
from sillage.kernel.types import (
    AuthorKind,
    ExtractionMode,
    Level,
    Role,
    Scope,
    ScopeKind,
    Source,
    Trace,
    TraceEvent,
)


class BlockingGuardrails:
    """A guardrail that refuses everything: nothing may be stored afterwards."""

    def __init__(self) -> None:
        self.scanned: list[str] = []

    def scan(self, text: str) -> GuardrailVerdict:
        self.scanned.append(text)
        return GuardrailVerdict(blocked=True, action="blocked", reasons=("prompt injection",))


class MaskingGuardrails:
    """A guardrail that masks: the capture keeps the masked text."""

    def scan(self, text: str) -> GuardrailVerdict:
        return GuardrailVerdict(action="masked", masked_text=text.replace("base", "***"))


class DownMemory:
    """A memory backend that is down: the capture degrades, it does not fail."""

    def create_events(self, **kwargs) -> str:
        raise BackendUnavailable("AgentCore Memory indisponible")


class DownBlobs:
    """An object store that is down: the capture keeps going without its copy."""

    def put(self, key: str, payload: str) -> str:
        raise BackendUnavailable("S3 indisponible")

    def get(self, key: str) -> str | None:
        return None

    def delete(self, key: str) -> int:
        return 0


def _fake_memory_module(backend: type) -> types.ModuleType:
    """A ``sillage.memory.local`` module holding one backend class."""
    module = types.ModuleType("sillage.memory.local")
    module.LocalMemoryBackend = backend
    return module


@pytest.fixture
def memory(settings, clock) -> OfflineMemoryBackend:
    return OfflineMemoryBackend(settings, clock)


@pytest.fixture
def catalog(clock) -> LocalCatalog:
    return LocalCatalog(clock)


@pytest.fixture
def blobs() -> LocalBlobStore:
    return LocalBlobStore()


@pytest.fixture
def service(settings, memory, catalog, blobs, clock) -> IngestService:
    return IngestService(settings=settings, memory=memory, catalog=catalog, blobs=blobs, clock=clock)


def do_not_keep(trace: Trace) -> Trace:
    """The same job, marked "a ne pas retenir" by its author (EF-CAP-12)."""
    return replace(trace, do_not_distill=True)


def many_events(count: int) -> Trace:
    """A trace of ``count`` events, to try the 100 events per call limit."""
    events = tuple(
        TraceEvent(role=Role.ASSISTANT, text=f"[ingénieur] Action {index}.", kind="action")
        for index in range(count)
    )
    return Trace(
        work_item_id="guichet:gros-ticket",
        scope=Scope(kind=ScopeKind.CLIENT, client_id="a"),
        source=Source.GUICHET,
        author_kind=AuthorKind.HUMAN,
        author=pseudonym("alice"),
        events=events,
    )


@pytest.mark.recette
class TestR3SameTraceTwice:
    """R3: one episode, ``duplicate=True``, and nothing rewritten."""

    def test_the_second_reception_says_duplicate(self, service) -> None:
        trace = sample.ticket_2041()
        first = service.ingest_trace(trace)
        second = service.ingest_trace(trace)
        assert first.duplicate is False and first.events_created is True
        assert second.duplicate is True and second.events_created is False

    def test_one_episode_only(self, service, catalog) -> None:
        trace = sample.ticket_2041()
        first = service.ingest_trace(trace)
        service.ingest_trace(trace)
        assert len(catalog.episodes) == 1
        assert catalog.get_episode(first.episode_id) is not None

    def test_the_raw_copy_is_not_rewritten(self, service, blobs) -> None:
        report = service.ingest_trace(sample.ticket_2041())
        service.ingest_trace(sample.ticket_2041())
        assert blobs.versions_of(report.raw_key) == 1

    def test_the_events_are_replayed_once(self, service, memory) -> None:
        trace = sample.ticket_2041()
        service.ingest_trace(trace)
        service.ingest_trace(trace)
        assert len(memory.events()) == len(trace.events)

    def test_the_report_of_a_duplicate_still_points_at_the_job(self, service) -> None:
        first = service.ingest_trace(sample.ticket_2041())
        second = service.ingest_trace(sample.ticket_2041())
        assert second.episode_id == first.episode_id
        assert second.raw_key == first.raw_key
        assert second.redacted_secrets == first.redacted_secrets

    def test_another_job_is_not_a_duplicate(self, service) -> None:
        service.ingest_trace(sample.ticket_2041())
        assert service.ingest_trace(sample.investigation_77()).duplicate is False


@pytest.mark.recette
class TestR4DoNotKeep:
    """R4 and EF-CAP-12: stored, replayed with SKIP, never distilled."""

    def test_the_events_are_replayed_with_skip(self, service, memory) -> None:
        service.ingest_trace(do_not_keep(sample.ticket_2041()))
        modes = {event.extraction_mode for event in memory.events()}
        assert modes == {ExtractionMode.SKIP}

    def test_the_trace_is_still_stored(self, service, catalog, blobs) -> None:
        report = service.ingest_trace(do_not_keep(sample.ticket_2041()))
        assert report.do_not_distill is True and report.events_created is True
        assert blobs.get(report.raw_key)
        stored = catalog.get_trace(report.trace.work_item_id)
        assert stored is not None and stored.do_not_distill is True

    def test_no_episode_is_distilled(self, service, catalog) -> None:
        report = service.ingest_trace(do_not_keep(sample.ticket_2041()))
        assert catalog.get_episode(report.episode_id) is None
        assert len(catalog.episodes) == 0

    def test_the_capture_state_says_skipped(self, service, catalog) -> None:
        report = service.ingest_trace(do_not_keep(sample.ticket_2041()))
        assert catalog.get_trace(report.trace.work_item_id).origin["captureState"] == CAPTURE_SKIPPED

    def test_a_kept_job_is_replayed_with_the_default_mode(self, service, memory) -> None:
        service.ingest_trace(sample.ticket_2041())
        assert {event.extraction_mode for event in memory.events()} == {ExtractionMode.DEFAULT}


class TestMarkDoNotKeep:
    """The author changes their mind after the capture (17.1, EF-CAP-12)."""

    def test_the_job_is_flagged_and_the_episode_withdrawn(self, service, catalog) -> None:
        report = service.ingest_trace(sample.ticket_2041())
        service.mark_do_not_keep(report.trace.work_item_id, by="alice")
        flagged = catalog.get_trace(report.trace.work_item_id)
        assert flagged.do_not_distill is True
        assert flagged.origin["captureState"] == CAPTURE_SKIPPED
        assert catalog.get_episode(report.episode_id).level is Level.WITHDRAWN

    def test_who_asked_is_a_pseudonym(self, service, catalog) -> None:
        report = service.ingest_trace(sample.ticket_2041())
        service.mark_do_not_keep(report.trace.work_item_id, by="alice")
        stored = catalog.get_trace(report.trace.work_item_id)
        assert stored.origin["doNotKeepBy"] == pseudonym("alice")
        assert "alice" not in json.dumps(stored.origin, ensure_ascii=False)

    def test_a_new_reception_honours_the_decision(self, service, catalog) -> None:
        report = service.ingest_trace(sample.ticket_2041())
        service.mark_do_not_keep(report.trace.work_item_id, by="alice")
        again = service.ingest_trace(sample.ticket_2041())
        assert again.duplicate is True and again.do_not_distill is True
        assert catalog.get_episode(report.episode_id).level is Level.WITHDRAWN
        assert len(catalog.episodes) == 1

    def test_an_unknown_job_is_refused_explicitly(self, service) -> None:
        with pytest.raises(NotFound):
            service.mark_do_not_keep("guichet:inconnu", by="alice")


@pytest.mark.recette
class TestR11RejectedDiagnosis:
    """R11: the episode keeps the rejected diagnosis, the reason and the right one."""

    def test_the_episode_keeps_the_rejected_diagnosis(self, service, catalog) -> None:
        report = service.ingest_trace(sample.investigation_77())
        episode = catalog.get_episode(report.episode_id)
        assert episode.rejected_diagnosis == "la base est saturée, il faut augmenter sa taille"

    def test_the_episode_keeps_the_reason(self, service, catalog) -> None:
        report = service.ingest_trace(sample.investigation_77())
        episode = catalog.get_episode(report.episode_id)
        assert "le pool a été doublé par le déploiement" in episode.justification

    def test_the_episode_keeps_the_right_diagnosis(self, service, catalog) -> None:
        report = service.ingest_trace(sample.investigation_77())
        episode = catalog.get_episode(report.episode_id)
        told = " ".join(turn.action for turn in episode.turns)
        assert "fuite de connexions depuis le déploiement" in told
        assert "rejeté par un ingénieur" in told

    def test_the_correction_is_attached_to_a_pseudonym(self, service, catalog) -> None:
        report = service.ingest_trace(sample.investigation_77())
        episode = catalog.get_episode(report.episode_id)
        assert episode.corrected_by == pseudonym("alice")

    def test_one_mixed_episode_for_the_whole_work(self, service, catalog) -> None:
        report = service.ingest_trace(sample.investigation_77())
        episode = catalog.get_episode(report.episode_id)
        assert episode.author_kind.value == "mixed"
        assert report.trace.origin["partAgent"] == "1"
        assert report.trace.origin["partHumain"] == "1"

    def test_an_accepted_session_names_no_corrector(self, service, catalog) -> None:
        report = service.ingest_trace(sample.kiro_session())
        episode = catalog.get_episode(report.episode_id)
        assert episode.corrected_by == ""
        assert episode.rejected_diagnosis == ""
        assert "chaîne est verte" in episode.justification


class TestEF13RawCopy:
    """EF-CAP-13: a versioned copy of the normalised trace, for audit."""

    def test_the_copy_holds_the_whole_trace(self, service, blobs) -> None:
        report = service.ingest_trace(sample.ticket_2041())
        stored = json.loads(blobs.get(report.raw_key))
        assert stored["workItemId"] == report.trace.work_item_id
        assert len(stored["events"]) == len(report.trace.events)

    def test_the_copy_is_clean_of_secrets(self, service, blobs) -> None:
        report = service.ingest_trace(sample.ticket_2041())
        stored = blobs.get(report.raw_key)
        assert sample.PLANTED_ACCESS_KEY not in stored
        assert "[REDACTED:aws_access_key]" in stored

    def test_the_work_row_points_at_its_copy(self, service, catalog) -> None:
        report = service.ingest_trace(sample.ticket_2041())
        stored = catalog.get_trace(report.trace.work_item_id)
        assert stored.origin["rawKey"] == report.raw_key
        assert stored.origin["captureState"] == CAPTURE_EVENTS


class TestGuardrails:
    """Chapter 10.3: the screen happens before anything is stored."""

    def test_a_blocked_trace_stores_nothing(self, settings, memory, catalog, blobs, clock) -> None:
        guardrails = BlockingGuardrails()
        service = IngestService(
            settings=settings,
            memory=memory,
            catalog=catalog,
            blobs=blobs,
            guardrails=guardrails,
            clock=clock,
        )
        with pytest.raises(PoisonedContent):
            service.ingest_trace(sample.ticket_2041())
        assert blobs.keys() == ()
        assert catalog.traces == {} and catalog.episodes == {} and catalog.fingerprints == {}
        assert memory.events() == ()
        assert guardrails.scanned, "le garde-fou n'a rien vu"

    def test_a_masked_trace_is_stored_masked(self, settings, memory, catalog, blobs, clock) -> None:
        service = IngestService(
            settings=settings,
            memory=memory,
            catalog=catalog,
            blobs=blobs,
            guardrails=MaskingGuardrails(),
            clock=clock,
        )
        report = service.ingest_trace(sample.ticket_2041())
        assert "base" not in report.trace.events[1].text
        assert "***" in report.trace.events[1].text

    def test_the_screen_runs_after_the_redaction(self, settings, memory, catalog, blobs, clock) -> None:
        guardrails = BlockingGuardrails()
        service = IngestService(
            settings=settings,
            memory=memory,
            catalog=catalog,
            blobs=blobs,
            guardrails=guardrails,
            clock=clock,
        )
        with pytest.raises(PoisonedContent):
            service.ingest_trace(sample.ticket_2041())
        assert all(sample.PLANTED_ACCESS_KEY not in text for text in guardrails.scanned)


class TestDegraded:
    """ENF-DISP-02 and R21: a backend down costs a copy, not the capture."""

    def test_a_memory_down_still_stores_the_job(self, settings, catalog, blobs, clock) -> None:
        service = IngestService(
            settings=settings, memory=DownMemory(), catalog=catalog, blobs=blobs, clock=clock
        )
        report = service.ingest_trace(sample.ticket_2041())
        assert report.events_created is False
        assert catalog.get_trace(report.trace.work_item_id) is not None
        assert catalog.get_trace(report.trace.work_item_id).origin["captureState"] == CAPTURE_STORED
        assert blobs.get(report.raw_key)

    def test_an_object_store_down_still_replays_the_events(self, settings, memory, catalog, clock) -> None:
        service = IngestService(
            settings=settings, memory=memory, catalog=catalog, blobs=DownBlobs(), clock=clock
        )
        report = service.ingest_trace(sample.ticket_2041())
        assert report.events_created is True
        assert len(memory.events()) == len(sample.ticket_2041().events)


class TestIngestRaw:
    """``ingest_raw``: a payload of any source, normalised then ingested."""

    @pytest.mark.parametrize(
        ("source", "payload"),
        [
            (Source.GUICHET, sample.ticket_2041_payload()),
            (Source.DEVOPS_AGENT, sample.investigation_77_payload()),
            (Source.KIRO, sample.kiro_session_payload()),
            (Source.MERGE, sample.merged_fix_payload()),
        ],
    )
    def test_a_payload_gives_the_same_job_as_its_trace(self, service, source, payload) -> None:
        report = service.ingest_raw(source, payload)
        assert report.duplicate is False and report.events_created is True
        assert report.trace.source is source
        assert report.trace == service.catalog.get_trace(report.trace.work_item_id)

    def test_the_same_payload_twice_is_a_duplicate(self, service) -> None:
        payload = sample.ticket_2041_payload()
        assert service.ingest_raw(Source.GUICHET, payload).duplicate is False
        assert service.ingest_raw(Source.GUICHET, payload).duplicate is True


class TestOfflineWiring:
    """``build_default_service``: 100 % offline, no argument required."""

    def test_it_builds_without_any_argument(self) -> None:
        service = build_default_service()
        assert isinstance(service, IngestService)
        assert service.ingest_trace(sample.ticket_2041()).events_created is True

    def test_it_needs_no_aws_and_no_network(self) -> None:
        build_default_service()
        for module in ("boto3", "botocore", "fastapi", "strands", "httpx"):
            assert module not in sys.modules, f"{module} importe par la capture"

    def test_every_part_can_be_replaced(self, settings, catalog, blobs, memory) -> None:
        service = build_default_service(
            settings=settings, catalog=catalog, blobs=blobs, memory=memory, guardrails=None
        )
        assert service.catalog is catalog and service.blobs is blobs and service.memory is memory

    def test_the_memory_fallback_respects_the_port(self, settings) -> None:
        backend = default_memory(settings)
        assert isinstance(backend, MemoryBackend)

    def test_a_memory_that_cannot_be_built_falls_back(self, settings, monkeypatch) -> None:
        class Broken:
            def __init__(self, *args, **kwargs) -> None:
                raise ValueError("configuration manquante")

        monkeypatch.setitem(sys.modules, "sillage.memory.local", _fake_memory_module(Broken))
        assert isinstance(default_memory(settings), OfflineMemoryBackend)

    def test_a_usable_memory_is_preferred_over_the_fallback(self, settings, monkeypatch) -> None:
        class Usable:
            def __init__(self, given, clock=None) -> None:
                self.given = given

        monkeypatch.setitem(sys.modules, "sillage.memory.local", _fake_memory_module(Usable))
        backend = default_memory(settings)
        assert isinstance(backend, Usable) and backend.given is settings

    def test_the_catalog_of_the_default_service_holds_the_blobs(self) -> None:
        service = build_default_service()
        report = service.ingest_trace(sample.ticket_2041())
        assert service.blobs.get(report.raw_key)
        assert service.catalog.get_episode(report.episode_id) is not None


class TestOfflineMemoryBackend:
    """The recorder itself: the port, the batches, the forgetting."""

    def test_it_replays_every_event(self, memory) -> None:
        trace = sample.ticket_2041()
        record = memory.create_events(actor_id="client-a", session_id=trace.session_id, trace=trace)
        assert record
        assert len(memory.events(session_id=trace.session_id)) == len(trace.events)

    def test_it_respects_the_batch_limit(self, settings, clock) -> None:
        backend = OfflineMemoryBackend(settings, clock)
        trace = many_events(settings.max_events_per_call + 50)
        backend.create_events(actor_id="client-a", session_id=trace.session_id, trace=trace)
        stored = backend.events()
        assert len(stored) == settings.max_events_per_call + 50
        assert max(event.batch for event in stored) == 2
        assert sum(1 for event in stored if event.batch == 1) == settings.max_events_per_call

    def test_it_reads_back_what_it_stored(self, memory) -> None:
        trace = sample.ticket_2041()
        memory.create_events(actor_id="client-a", session_id=trace.session_id, trace=trace)
        namespace = memory.namespace("client-a", trace.session_id)
        assert memory.retrieve(namespace=namespace, query="connexions base", top_k=3)
        assert memory.list_records(namespace_path=namespace)
        assert memory.reflections(actor_id="client-a") == ()
        memory.put_records([MemoryRecord(record_id="f-1", namespace=namespace, content="texte approuvé")])
        assert [record.record_id for record in memory.records()] == ["f-1"]

    def test_it_forgets_one_job(self, memory) -> None:
        trace = sample.ticket_2041()
        memory.create_events(actor_id="client-a", session_id=trace.session_id, trace=trace)
        removed = memory.delete_records(actor_id="client-a", session_id=trace.session_id)
        assert removed == len(trace.events)
        assert memory.events() == ()

    def test_it_ensures_a_memory_once(self, memory, settings) -> None:
        first = memory.ensure_memory(settings.memory_name, settings.memory_description)
        assert first and first == memory.ensure_memory("autre", "autre")


class TestIngestReport:
    """The report says what happened, so no caller has to guess."""

    def test_it_carries_the_whole_story(self, service) -> None:
        report = service.ingest_trace(sample.ticket_2041())
        assert isinstance(report, IngestReport)
        assert report.redacted_secrets == 3
        assert report.episode_id == episode_id("client-a", report.trace.work_item_id)
        assert report.raw_key.startswith("traces/client-a/")
        assert report.trace.received_at is not None

    def test_the_metadata_of_a_replayed_event_stays_small(self, service, memory, settings) -> None:
        service.ingest_trace(sample.ticket_2041())
        for event in memory.events():
            assert len(event.role) and event.kind
        assert len(sample.ticket_2041().metadata) <= settings.max_event_metadata
