"""Fakes and builders for the memory tests: no network, no model, no boto3.

Three things live here so the five test modules of the memory share one
vocabulary instead of five copies:

* :class:`ScriptedModel` - a ``ModelBackend`` that answers from a script and
  records every call, so a test can check the prompt, the tier and the schema;
* :func:`job_trace` - a finished job in the shape of chapter 13.1, with as many
  steps as a test needs (a trace of 250 events is the only way to exercise the
  100 events per call limit of annex B);
* :class:`FakeMemoryClient` and :class:`FakeControlClient` - the two planes of
  AgentCore Memory, recording the payloads they are given.

They are **shape** fakes, not emulators: they exist to prove the requests are the
ones of the annexes, which is what a real service would reject or mis-answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from conftest import FIXED_NOW as MOMENT
from sillage.kernel.protocols import MemoryBackend, MemoryRecord
from sillage.kernel.types import (
    AuthorKind,
    ExtractionMode,
    Fiche,
    Level,
    ModelTier,
    Role,
    Scope,
    ScopeKind,
    Source,
    Trace,
    TraceEvent,
)

#: The closing of a job that worked, in the words the capture reads.
CLOSED_WITH_SUCCESS = "Ticket clos. Résolution confirmée par le demandeur."
#: The closing of a job that did not work.
CLOSED_WITHOUT_SUCCESS = "Ticket fermé sans résolution, le demandeur a abandonné."


class ScriptedModel:
    """A ``ModelBackend`` that answers from a script: text only, never a number."""

    def __init__(
        self,
        *,
        distil: str = "",
        reflect: str = "",
        intent: str = "",
        fails: bool = False,
    ) -> None:
        self.distil = distil
        self.reflect = reflect
        self.intent = intent
        self.fails = fails
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        prompt: str,
        system: str = "",
        tier: ModelTier = ModelTier.FAST,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        """Answer the prompt that was asked, and keep a copy of the call."""
        self.calls.append({"prompt": prompt, "system": system, "tier": tier, "json_schema": json_schema})
        if self.fails:
            raise RuntimeError("modele indisponible")
        if "Reformule" in prompt:
            return self.intent
        if "même périmètre" in prompt:
            return self.reflect
        return self.distil

    @property
    def prompts(self) -> list[str]:
        """Every prompt the code sent, in order."""
        return [str(call["prompt"]) for call in self.calls]


def job_trace(
    *,
    work_item_id: str,
    client: str = "a",
    technologies: tuple[str, ...] = ("postgresql",),
    situation: str = "Le site renvoie des erreurs 500.",
    steps: Sequence[tuple[str, str]] = (),
    closing: str = CLOSED_WITH_SUCCESS,
    source: Source = Source.GUICHET,
    author_kind: AuthorKind = AuthorKind.HUMAN,
    author: str = "p-7f3a",
    at: datetime = MOMENT,
    origin: dict[str, str] | None = None,
    do_not_distill: bool = False,
) -> Trace:
    """One finished job, normalised the way chapter 13.1 says."""
    events = [TraceEvent(role=Role.USER, text=f"[alarme] {situation}", kind="request", at=at)]
    for action, result in steps:
        events.append(TraceEvent(role=Role.ASSISTANT, text=f"[ingénieur] {action}", kind="action", at=at))
        events.append(TraceEvent(role=Role.TOOL, text=f"[commande] {result}", kind="tool_output", at=at))
    events.append(TraceEvent(role=Role.OTHER, text=f"[système] {closing}", kind="system", at=at))
    return Trace(
        work_item_id=work_item_id,
        scope=Scope(kind=ScopeKind.CLIENT, client_id=client),
        source=source,
        author_kind=author_kind,
        author=author,
        events=tuple(events),
        technologies=technologies,
        closed_at=at,
        do_not_distill=do_not_distill,
        origin=dict(origin or {}),
    )


def many_step_trace(count: int, *, work_item_id: str = "guichet:gros-ticket", client: str = "a") -> Trace:
    """A trace of ``count`` steps: two events each, to cross the batch limit."""
    steps = [(f"Action numéro {index} sur la base.", f"Sortie numéro {index}.") for index in range(count)]
    return job_trace(work_item_id=work_item_id, client=client, steps=steps)


def record(
    *,
    record_id: str,
    namespace: str,
    content: str = "",
    score: float = 0.0,
    metadata: dict[str, str] | None = None,
    created_at: datetime | None = MOMENT,
) -> MemoryRecord:
    """One memory record, the way the kernel describes it."""
    return MemoryRecord(
        record_id=record_id,
        namespace=namespace,
        content=content,
        score=score,
        metadata=dict(metadata or {}),
        created_at=created_at,
    )


def summary_of(found: MemoryRecord, *, score: float | None = None) -> dict:
    """The ``memoryRecordSummaries`` shape the service answers with."""
    return {
        "recordId": found.record_id,
        "namespace": found.namespace,
        "content": {"text": found.content},
        "createdAt": found.created_at,
        "score": found.score if score is None else score,
        "metadata": {key: {"stringValue": value} for key, value in found.metadata.items()},
    }


class ClientError(Exception):
    """A boto3 client error: it carries the code the service would answer with."""

    def __init__(self, code: str = "ConflictException", message: str = "la mémoire existe déjà") -> None:
        super().__init__(message)
        self.response = {"Error": {"Code": code, "Message": message}}


class _RecordingClient:
    """What the two planes share: every call is kept, nothing is sent."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def operations(self) -> list[str]:
        """The names of the calls, in the order they were made."""
        return [name for name, _ in self.calls]

    def payload_of(self, operation: str) -> dict:
        """The payload of the first call of one operation."""
        for name, kwargs in self.calls:
            if name == operation:
                return kwargs
        raise AssertionError(f"aucun appel {operation!r} n'a été émis")

    def payloads_of(self, operation: str) -> list[dict]:
        """Every payload of one operation, in order."""
        return [kwargs for name, kwargs in self.calls if name == operation]


class FakeMemoryClient(_RecordingClient):
    """The data plane: retrieve, batch create, list and delete the records."""

    def __init__(self, *, summaries: Sequence[dict] = (), pages: int = 1, fails: str = "") -> None:
        super().__init__()
        self.summaries = list(summaries)
        self.pages = max(int(pages), 1)
        self.fails = fails

    def _note(self, operation: str, kwargs: dict) -> None:
        self.calls.append((operation, kwargs))
        if self.fails:
            raise ClientError(self.fails)

    def retrieve_memory_records(self, **kwargs: Any) -> dict:
        self._note("retrieve_memory_records", kwargs)
        return {"memoryRecordSummaries": list(self.summaries)}

    def list_memory_records(self, **kwargs: Any) -> dict:
        self._note("list_memory_records", kwargs)
        if self.pages > 1 and "nextToken" not in kwargs:
            return {"memoryRecordSummaries": list(self.summaries), "nextToken": "page-2"}
        return {"memoryRecordSummaries": list(self.summaries)}

    def batch_create_memory_records(self, **kwargs: Any) -> dict:
        self._note("batch_create_memory_records", kwargs)
        return {}

    def delete_memory_records(self, **kwargs: Any) -> dict:
        self._note("delete_memory_records", kwargs)
        return {"deletedRecordIds": list(kwargs.get("recordIds") or [])}


class FakeControlClient(_RecordingClient):
    """The control plane: create the memory, replay the events."""

    def __init__(
        self,
        *,
        memory_id: str = "mem-1",
        strategy_id: str = "strat-1",
        name: str = "sillage_memoire",
        conflict: bool = False,
    ) -> None:
        super().__init__()
        self.memory_id = memory_id
        self.strategy_id = strategy_id
        self.name = name
        self.conflict = conflict

    def create_memory(self, **kwargs: Any) -> dict:
        self.calls.append(("create_memory", kwargs))
        if self.conflict:
            raise ClientError("ConflictException", "la mémoire existe déjà")
        return self.memory_answer()

    def create_event(self, **kwargs: Any) -> dict:
        self.calls.append(("create_event", kwargs))
        return {"eventIds": [f"evt-{len(self.calls)}"]}

    def list_memories(self, **kwargs: Any) -> dict:
        self.calls.append(("list_memories", kwargs))
        return {"memorySummaries": [{"memoryId": self.memory_id, "name": self.name}]}

    def get_memory(self, **kwargs: Any) -> dict:
        self.calls.append(("get_memory", kwargs))
        return self.memory_answer()

    def memory_answer(self) -> dict:
        """The answer of the service: an id, and the strategy it created."""
        return {
            "memoryId": self.memory_id,
            "memoryStrategies": [{"episodicMemoryStrategy": {"strategyId": self.strategy_id}}],
        }


#: The connection trouble of annex B, told three times: three close episodes.
CONNECTION_STEPS: tuple[tuple[str, str], ...] = (
    (
        "Je redémarre le service pour libérer les connexions de la base.",
        "Le redémarrage ne règle rien, les connexions de la base remontent à 100.",
    ),
    (
        "Je reviens à la taille du groupe de connexions d'avant le déploiement.",
        "Les connexions de la base sont stables à 38, plus d'erreur 500.",
    ),
)


def connection_job(number: int, client: str = "a") -> Trace:
    """One ticket about the connections of a database, after a deployment."""
    return job_trace(
        work_item_id=f"guichet:ticket-{2041 + number}",
        client=client,
        technologies=("postgresql", "ecs"),
        situation=(
            "Le site renvoie des erreurs 500, les journaux indiquent too many connections "
            "sur la base après le déploiement."
        ),
        steps=CONNECTION_STEPS,
    )


def proxy_job(number: int, client: str = "a") -> Trace:
    """One ticket about a reverse proxy: another technological family."""
    return job_trace(
        work_item_id=f"guichet:proxy-{number}",
        client=client,
        technologies=("nginx",),
        situation="Le mandataire renvoie des 502 sur la route de paiement.",
        steps=(("Je recharge la configuration du mandataire.", "Les 502 continuent sur la route."),),
    )


def replay(backend: MemoryBackend, trace: Trace, *, mode: ExtractionMode = ExtractionMode.DEFAULT) -> str:
    """Replay one job in a memory backend, the way the capture does."""
    return backend.create_events(
        actor_id=trace.scope.actor_id,
        session_id=trace.session_id,
        trace=trace,
        extraction_mode=mode,
    )


def client_space(backend: MemoryBackend, client: str = "a") -> str:
    """The prefix that covers every session of one client."""
    return backend.settings.reflection_namespace(f"client-{client}")


def fiche_of(
    *,
    fiche_id: str,
    scope: Scope,
    title: str = "Rétablir les connexions de la base",
    body: str = "",
    level: Level = Level.VALIDATED,
    memory_record_id: str = "",
    successes: int = 0,
    failures: int = 0,
    last_confirmation: datetime | None = None,
) -> Fiche:
    """One catalogue row, the way the code that ranks the hits reads it."""
    return Fiche(
        fiche_id=fiche_id,
        scope=scope,
        title=title,
        body=body or title,
        level=level,
        memory_record_id=memory_record_id or None,
        success_count=successes,
        failure_count=failures,
        last_confirmation=last_confirmation,
    )


__all__ = [
    "CLOSED_WITHOUT_SUCCESS",
    "CLOSED_WITH_SUCCESS",
    "CONNECTION_STEPS",
    "MOMENT",
    "ClientError",
    "FakeControlClient",
    "FakeMemoryClient",
    "ScriptedModel",
    "client_space",
    "connection_job",
    "fiche_of",
    "job_trace",
    "many_step_trace",
    "proxy_job",
    "record",
    "replay",
    "summary_of",
]
