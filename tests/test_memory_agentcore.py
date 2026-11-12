"""``AgentCoreMemoryBackend`` against fake clients: the annexes, and no network.

Nothing here talks to AWS. Two fake objects answer like the two planes of
AgentCore Memory and record every payload, so what is checked is exactly what the
service would reject: the shape of ``CreateMemory`` (annex A), of ``CreateEvent``
(annex B, 100 elements per call, one stable client token per batch), of
``RetrieveMemoryRecords`` (annex C), the walk by ``namespacePath``, the verbatim
write of an approved text, and the deletion of a job.

The rule of CONTRACT.md 3 is checked by reading the source: **no module of the
memory imports boto3 at module level**, and importing this module leaves boto3
out of ``sys.modules``.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import sillage.memory
from memory_fakes import (
    MOMENT,
    ClientError,
    FakeControlClient,
    FakeMemoryClient,
    job_trace,
    many_step_trace,
    record,
    summary_of,
)
from sillage.capture import sample
from sillage.kernel.config import EPISODE_NAMESPACE, REFLECTION_NAMESPACE, Settings
from sillage.kernel.errors import BackendUnavailable
from sillage.kernel.protocols import MemoryBackend
from sillage.kernel.types import ExtractionMode, Level, Scope, ScopeKind
from sillage.memory.agentcore import AgentCoreMemoryBackend, lesson_of_record, strategy_id_of

#: The fields of annex A, in the order the API reference gives them.
ANNEX_A_STRATEGY_KEYS = {
    "name",
    "description",
    "namespaceTemplates",
    "reflectionConfiguration",
    "recordExpirySpecification",
}

#: The fields of annex B.
ANNEX_B_KEYS = {"actorId", "sessionId", "eventTimestamp", "clientToken", "metadata", "payload"}

#: The roles AgentCore Memory accepts (specification 13.1).
ANNEX_B_ROLES = {"USER", "ASSISTANT", "TOOL", "OTHER"}


@pytest.fixture
def data() -> FakeMemoryClient:
    return FakeMemoryClient()


@pytest.fixture
def control() -> FakeControlClient:
    return FakeControlClient()


@pytest.fixture
def backend(settings, data, control) -> AgentCoreMemoryBackend:
    return AgentCoreMemoryBackend(settings, client=data, control_client=control)


def _imports_boto3(node: ast.AST) -> bool:
    """True when one import node asks for boto3."""
    if isinstance(node, ast.Import):
        return any(alias.name.split(".")[0] == "boto3" for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").split(".")[0] == "boto3"
    return False


def _boto3_imports(tree: ast.AST) -> list[ast.AST]:
    """Every ``import boto3`` of a parsed module."""
    return [node for node in ast.walk(tree) if _imports_boto3(node)]


def _inside_a_function(tree: ast.AST, target: ast.AST) -> bool:
    """True when a node lives inside a function body, not at module level."""
    for node in ast.walk(tree):
        function = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        if function and any(child is target for child in ast.walk(node)):
            return True
    return False


class TestNoBoto3AtModuleLevel:
    """CONTRACT.md rule 3: the AWS import is lazy, so the suite stays offline."""

    def test_no_memory_module_imports_boto3_at_module_level(self) -> None:
        root = Path(sillage.memory.__file__).resolve().parent
        sources = sorted(root.glob("*.py"))
        assert sources, "aucun module trouvé"
        for source in sources:
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in _boto3_imports(tree):
                assert _inside_a_function(tree, node), f"{source.name} importe boto3 au niveau module"

    def test_importing_the_module_does_not_import_boto3(self) -> None:
        assert "boto3" not in sys.modules
        assert "botocore" not in sys.modules

    def test_building_the_backend_with_fake_clients_does_not_import_boto3(self, backend) -> None:
        assert isinstance(backend, MemoryBackend)
        assert "boto3" not in sys.modules

    def test_the_lazy_attribute_of_the_package_does_not_import_boto3(self) -> None:
        assert sillage.memory.AgentCoreMemoryBackend is AgentCoreMemoryBackend
        assert "boto3" not in sys.modules


class TestAnnexA:
    """``CreateMemory``: the exact shape of the annex."""

    def test_the_payload_is_the_one_of_the_annex(self, backend, control, settings) -> None:
        backend.ensure_memory(settings.memory_name, settings.memory_description)
        sent = control.payload_of("create_memory")
        assert sent == {
            "name": "sillage_memoire",
            "description": "Memoire partagee de l'equipe : ingenieurs, developpeurs, agents",
            "eventExpiryDuration": 90,
            "memoryStrategies": [
                {
                    "episodicMemoryStrategy": {
                        "name": "episodes_travail",
                        "description": "Un episode par travail termine, des lecons par perimetre",
                        "namespaceTemplates": [EPISODE_NAMESPACE],
                        "reflectionConfiguration": {"namespaceTemplates": [REFLECTION_NAMESPACE]},
                        "recordExpirySpecification": {"recordExpiryDurationDays": 730},
                    }
                }
            ],
        }

    def test_the_memory_is_created_once(self, backend, control) -> None:
        assert backend.ensure_memory("sillage_memoire", "x") == "mem-1"
        assert backend.ensure_memory("autre", "autre") == "mem-1"
        assert control.operations().count("create_memory") == 1

    def test_the_strategy_id_of_the_answer_is_kept(self, backend, control) -> None:
        backend.ensure_memory("sillage_memoire", "x")
        assert backend.strategy_id == "strat-1"
        assert strategy_id_of(control.memory_answer()) == "strat-1"

    def test_an_existing_memory_is_reused(self, settings) -> None:
        control = FakeControlClient(conflict=True)
        backend = AgentCoreMemoryBackend(settings, client=FakeMemoryClient(), control_client=control)
        assert backend.ensure_memory("sillage_memoire", "x") == "mem-1"
        assert control.operations() == ["create_memory", "list_memories", "get_memory"]

    def test_another_failure_is_not_swallowed(self, settings) -> None:
        control = FakeControlClient()
        control.create_memory = _raises("AccessDeniedException")
        backend = AgentCoreMemoryBackend(settings, client=FakeMemoryClient(), control_client=control)
        with pytest.raises(BackendUnavailable) as caught:
            backend.ensure_memory("sillage_memoire", "x")
        assert caught.value.details["operation"] == "create_memory"
        assert caught.value.details["aws_code"] == "AccessDeniedException"


def _raises(code: str):
    """A call that always fails with one client error code."""

    def call(**kwargs):
        raise ClientError(code)

    return call


class TestAnnexB:
    """``CreateEvent``: the exact shape of the annex, and its 100 element limit."""

    def test_the_payload_of_the_reference_ticket(self, backend, control) -> None:
        trace = sample.ticket_2041()
        backend.create_events(actor_id="client-a", session_id="ticket-2041", trace=trace)
        sent = control.payload_of("create_event")
        assert set(sent) == ANNEX_B_KEYS
        assert sent["actorId"] == "client-a"
        assert sent["sessionId"] == "ticket-2041"
        assert sent["clientToken"] == "ticket-2041-part-1"
        assert sent["eventTimestamp"] == int(trace.events[0].at.timestamp())

    def test_the_metadata_is_wrapped_in_string_values(self, backend, control) -> None:
        backend.create_events(actor_id="client-a", session_id="ticket-2041", trace=sample.ticket_2041())
        metadata = control.payload_of("create_event")["metadata"]
        assert metadata
        assert len(metadata) <= 15
        assert all(value == {"stringValue": str(value["stringValue"])} for value in metadata.values())
        assert metadata["source"] == {"stringValue": "guichet"}

    def test_the_payload_is_a_list_of_conversational_elements(self, backend, control) -> None:
        trace = sample.ticket_2041()
        backend.create_events(actor_id="client-a", session_id="ticket-2041", trace=trace)
        payload = control.payload_of("create_event")["payload"]
        assert len(payload) == len(trace.events)
        for element, event in zip(payload, trace.events, strict=True):
            assert set(element) == {"conversational"}
            conversational = element["conversational"]
            assert set(conversational) == {"role", "content"}
            assert conversational["role"] == event.role.value in ANNEX_B_ROLES
            assert conversational["content"] == {"text": event.text}

    def test_a_long_trace_is_cut_in_batches_of_a_hundred(self, backend, control, settings) -> None:
        limit = settings.max_events_per_call
        trace = many_step_trace(limit + 50)
        backend.create_events(actor_id="client-a", session_id="gros", trace=trace)
        payloads = control.payloads_of("create_event")
        total = len(trace.events)
        assert total == 2 * (limit + 50) + 2
        assert len(payloads) == -(-total // limit)
        assert all(len(sent["payload"]) <= limit for sent in payloads)
        assert sum(len(sent["payload"]) for sent in payloads) == total
        assert [sent["clientToken"] for sent in payloads] == [
            f"gros-part-{index}" for index in range(1, len(payloads) + 1)
        ]

    def test_the_client_token_is_stable_per_batch(self, backend, control) -> None:
        trace = many_step_trace(60)
        backend.create_events(actor_id="client-a", session_id="gros", trace=trace)
        first = [sent["clientToken"] for sent in control.payloads_of("create_event")]
        backend.create_events(actor_id="client-a", session_id="gros", trace=trace)
        again = [sent["clientToken"] for sent in control.payloads_of("create_event")][len(first) :]
        assert first == again == ["gros-part-1", "gros-part-2"]

    def test_a_job_marked_do_not_keep_is_replayed_with_skip(self, backend, control) -> None:
        trace = job_trace(work_item_id="guichet:secret", steps=(("Je regarde.", "Rien."),))
        backend.create_events(
            actor_id="client-a",
            session_id=trace.session_id,
            trace=trace,
            extraction_mode=ExtractionMode.SKIP,
        )
        assert control.payload_of("create_event")["extractionMode"] == "SKIP"

    def test_a_kept_job_carries_no_extraction_mode(self, backend, control) -> None:
        """The payload of a normal job is the annex, and nothing more."""
        backend.create_events(actor_id="client-a", session_id="ticket-2041", trace=sample.ticket_2041())
        assert "extractionMode" not in control.payload_of("create_event")

    def test_an_empty_trace_sends_nothing(self, backend, control) -> None:
        trace = replace(job_trace(work_item_id="guichet:vide", steps=()), events=())
        backend.create_events(actor_id="client-a", session_id="vide", trace=trace)
        assert control.payloads_of("create_event") == []


class TestAnnexC:
    """``RetrieveMemoryRecords``: the exact shape of the annex."""

    def test_the_payload_is_the_one_of_the_annex(self, backend, data, settings) -> None:
        deployed = Settings(strategy_id="strat-42")
        backend = AgentCoreMemoryBackend(
            deployed, client=data, control_client=FakeControlClient(strategy_id="strat-42")
        )
        backend.retrieve(
            namespace="/strategy/strat-42/actor/client-a/",
            query="Rétablir un service web qui épuise les connexions à sa base",
            top_k=5,
        )
        assert data.payload_of("retrieve_memory_records") == {
            "namespace": "/strategy/strat-42/actor/client-a/",
            "maxResults": 5,
            "searchCriteria": {
                "memoryStrategyId": "strat-42",
                "searchQuery": "Rétablir un service web qui épuise les connexions à sa base",
                "topK": 5,
            },
        }

    def test_the_answer_is_read_back_as_records(self, settings) -> None:
        found = record(record_id="ep-1", namespace="/commun/", content="Rétablir les connexions.")
        data = FakeMemoryClient(summaries=[summary_of(found, score=0.83)])
        backend = AgentCoreMemoryBackend(settings, client=data, control_client=FakeControlClient())
        hits = backend.retrieve(namespace="/commun/", query="connexions", top_k=3)
        assert len(hits) == 1
        assert hits[0].record_id == "ep-1"
        assert hits[0].content == "Rétablir les connexions."
        assert hits[0].score == pytest.approx(0.83)

    def test_the_strategy_name_is_used_before_the_memory_exists(self, backend, data) -> None:
        backend.retrieve(namespace="/commun/", query="connexions", top_k=1)
        criteria = data.payload_of("retrieve_memory_records")["searchCriteria"]
        assert criteria["memoryStrategyId"] == "episodes_travail"


class TestTheWalkAndTheWrites:
    """``ListMemoryRecords``, ``BatchCreateMemoryRecords``, ``DeleteMemoryRecords``."""

    def test_the_walk_uses_the_namespace_path(self, backend, data) -> None:
        backend.list_records(namespace_path="/strategy/episodes_travail/actor/client-a/session/")
        sent = data.payload_of("list_memory_records")
        assert sent["namespacePath"] == "/strategy/episodes_travail/actor/client-a/session/"
        assert sent["maxResults"] == 100

    def test_the_walk_follows_the_pages(self, settings) -> None:
        data = FakeMemoryClient(
            summaries=[summary_of(record(record_id="ep-1", namespace="/commun/"))], pages=2
        )
        backend = AgentCoreMemoryBackend(settings, client=data, control_client=FakeControlClient())
        found = backend.list_records(namespace_path="/commun/")
        assert len(found) == 2
        assert [sent.get("nextToken") for sent in data.payloads_of("list_memory_records")] == [
            None,
            "page-2",
        ]

    def test_the_approved_text_is_written_verbatim(self, backend, data) -> None:
        text = "Rétablir le pool de connexions : 10 par instance.\nÀ ne pas faire : redémarrer."
        backend.put_records([record(record_id="f-1", namespace="/fiches/client-a/", content=text)])
        sent = data.payload_of("batch_create_memory_records")
        assert sent["records"][0]["content"] == {"text": text}
        assert sent["records"][0]["namespace"] == "/fiches/client-a/"

    def test_nothing_is_written_for_an_empty_batch(self, backend, data) -> None:
        backend.put_records([])
        assert data.payloads_of("batch_create_memory_records") == []

    def test_the_deletion_lists_then_deletes(self, settings) -> None:
        namespace = "/strategy/episodes_travail/actor/client-a/session/ticket-2041/"
        summaries = [
            summary_of(record(record_id="ep-1", namespace=namespace)),
            summary_of(record(record_id="ep-2", namespace=namespace)),
        ]
        data = FakeMemoryClient(summaries=summaries)
        backend = AgentCoreMemoryBackend(settings, client=data, control_client=FakeControlClient())
        removed = backend.delete_records(actor_id="client-a", session_id="ticket-2041")
        assert removed == 2
        assert data.operations() == ["list_memory_records", "delete_memory_records"]
        assert data.payload_of("delete_memory_records") == {"recordIds": ["ep-1", "ep-2"]}
        assert data.payload_of("list_memory_records")["namespacePath"] == namespace

    def test_a_job_with_no_record_deletes_nothing(self, backend, data) -> None:
        assert backend.delete_records(actor_id="client-a", session_id="inconnu") == 0
        assert data.payloads_of("delete_memory_records") == []


class TestTheReflections:
    """The lessons of one perimeter, read in its own reflection space."""

    def test_the_walk_stays_inside_the_perimeter(self, backend, data) -> None:
        backend.reflections(actor_id="client-a")
        assert data.payload_of("list_memory_records")["namespacePath"] == (
            "/strategy/episodes_travail/actor/client-a/"
        )

    def test_the_lessons_of_another_client_are_never_read(self, backend, data) -> None:
        backend.reflections(actor_id="client-b")
        assert "client-a" not in data.payload_of("list_memory_records")["namespacePath"]

    def test_a_record_of_the_reflection_space_becomes_a_lesson(self, settings) -> None:
        summary = summary_of(
            record(
                record_id="le-1",
                namespace="/strategy/s/actor/client-a/",
                content="Rétablir les connexions de la base.",
                metadata={
                    "useCase": "Rétablir un service qui épuise ses connexions.",
                    "confidence": "0.72",
                    "episodeIds": "ep-1,ep-2,ep-3",
                },
            )
        )
        data = FakeMemoryClient(summaries=[summary])
        backend = AgentCoreMemoryBackend(settings, client=data, control_client=FakeControlClient())
        lessons = backend.reflections(actor_id="client-a")
        assert len(lessons) == 1
        lesson = lessons[0]
        assert lesson.lesson_id == "le-1"
        assert lesson.use_case == "Rétablir un service qui épuise ses connexions."
        assert lesson.episode_ids == ("ep-1", "ep-2", "ep-3")
        assert lesson.confidence == 0.72
        assert lesson.level is Level.PROPOSED
        assert lesson.scope == Scope(kind=ScopeKind.CLIENT, client_id="a")

    def test_the_confidence_stays_inside_its_range(self) -> None:
        scope = Scope(kind=ScopeKind.COMMON)
        for value, expected in (("9.9", 1.0), ("0.0", 0.1), ("pas-un-nombre", 0.5), ("", 0.5)):
            found = record(record_id="le-1", namespace="/commun/", metadata={"confidence": value})
            assert lesson_of_record(scope, found).confidence == expected


class TestTheDegradation:
    """ENF-DISP-02: a service that is down becomes ``BackendUnavailable``."""

    def test_a_failing_call_is_reported(self, settings) -> None:
        data = FakeMemoryClient(fails="ThrottlingException")
        backend = AgentCoreMemoryBackend(settings, client=data, control_client=FakeControlClient())
        with pytest.raises(BackendUnavailable) as caught:
            backend.retrieve(namespace="/commun/", query="connexions")
        assert caught.value.details["operation"] == "retrieve_memory_records"
        assert caught.value.details["aws_code"] == "ThrottlingException"
        assert caught.value.code == "backend_unavailable"

    def test_an_unexpected_answer_is_read_as_empty(self, settings) -> None:
        class Silent:
            def retrieve_memory_records(self, **kwargs):
                return None

        backend = AgentCoreMemoryBackend(settings, client=Silent(), control_client=FakeControlClient())
        assert backend.retrieve(namespace="/commun/", query="connexions") == ()


def test_the_backend_satisfies_the_port(backend) -> None:
    assert isinstance(backend, MemoryBackend)


def test_the_events_are_dated_from_the_first_element_of_the_batch(backend, control) -> None:
    trace = job_trace(work_item_id="guichet:date", steps=(("Je regarde.", "Rien."),))
    backend.create_events(actor_id="client-a", session_id=trace.session_id, trace=trace)
    assert control.payload_of("create_event")["eventTimestamp"] == int(MOMENT.timestamp())
