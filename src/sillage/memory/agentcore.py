"""The deployed memory backend: AgentCore Memory, over boto3.

boto3 is imported **inside the constructor**, never at module import time
(CONTRACT.md rule 3): the test suite, the smoke and the offline demo all import
this module with no dependency installed, and a fake client object can be
injected so the exact shapes of the annexes are checked without a single network
call.

The requests are built by :mod:`sillage.memory.agentcore_payloads`, which
reproduces annex A (``CreateMemory``), annex B (``CreateEvent``, 100 elements
maximum per call, one stable client token per batch) and annex C
(``RetrieveMemoryRecords``). Walking a perimeter uses ``namespacePath`` with
``ListMemoryRecords``, and the approved text of a fiche is written verbatim with
``BatchCreateMemoryRecords`` (specification 12.4).

Every call goes through :meth:`_call`, which turns a client error into
``BackendUnavailable`` so the callers degrade instead of failing (ENF-DISP-02).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sillage.kernel.config import DEFAULT, Settings
from sillage.kernel.errors import BackendUnavailable
from sillage.kernel.ids import lesson_id, make_id
from sillage.kernel.protocols import MemoryRecord
from sillage.kernel.types import ExtractionMode, Lesson, Level, Scope, Trace
from sillage.memory.agentcore_payloads import (
    MAX_ELEMENTS_PER_CALL,
    batch_create_records_payload,
    create_event_payload,
    create_memory_payload,
    delete_records_payload,
    event_metadata_payload,
    event_timestamp,
    list_records_payload,
    records_of,
    retrieve_payload,
)
from sillage.memory.local_records import batches_of
from sillage.memory.namespaces import reflection_namespace, scope_of_actor

#: Safety valve of the pagination: a service that repeats its token stops here.
MAX_PAGES = 50

#: The error codes that tell a memory already exists, so it is reused.
CONFLICT_CODES = ("conflict", "alreadyexists", "already_exists")


class AgentCoreMemoryBackend:
    """``MemoryBackend`` on AgentCore Memory: two clients, lazy boto3."""

    def __init__(
        self,
        settings: Settings = DEFAULT,
        client: Any = None,
        control_client: Any = None,
    ) -> None:
        self.settings = settings
        self.memory_id = ""
        self.strategy_id = settings.strategy_id
        if client is None:
            client = _boto3_client("bedrock-agentcore", settings)
        if control_client is None:
            control_client = _boto3_client("bedrock-agentcore-control", settings)
        #: Data plane: retrieve, batch create, list and delete the records.
        self._client = client
        #: Control plane: create the memory and replay the events.
        self._control = control_client

    # -- MemoryBackend ------------------------------------------------------ #
    def ensure_memory(self, name: str, description: str) -> str:
        """Create the memory of annex A, or reuse the one that already exists."""
        if self.memory_id:
            return self.memory_id
        payload = {**create_memory_payload(self.settings), "name": name, "description": description}
        answer = self._create_or_find(payload, name)
        self.memory_id = str(answer.get("memoryId") or "")
        self.strategy_id = self.strategy_id or strategy_id_of(answer)
        return self.memory_id

    def create_events(
        self,
        *,
        actor_id: str,
        session_id: str,
        trace: Trace,
        extraction_mode: ExtractionMode = ExtractionMode.DEFAULT,
    ) -> str:
        """Replay a trace as events: 100 elements per call, one token per batch."""
        metadata = event_metadata_payload(trace, self.settings)
        mode = "" if extraction_mode is ExtractionMode.DEFAULT else extraction_mode.value
        batches = batches_of(trace, min(self.settings.max_events_per_call, MAX_ELEMENTS_PER_CALL))
        fallback = datetime.now(UTC)
        for index, batch in enumerate(batches, start=1):
            payload = create_event_payload(
                actor_id=actor_id,
                session_id=session_id,
                batch=batch,
                metadata=metadata,
                timestamp=event_timestamp(batch, trace, fallback),
                index=index,
                extraction_mode=mode,
            )
            self._call(self._control, "create_event", **payload)
        return make_id("trace", actor_id, session_id, str(len(batches)))

    def retrieve(self, *, namespace: str, query: str, top_k: int = 5) -> Sequence[MemoryRecord]:
        """The records closest to an intention, inside one namespace (annex C)."""
        payload = retrieve_payload(
            namespace=namespace, query=query, top_k=top_k, strategy_id=self._strategy()
        )
        return records_of(self._call(self._client, "retrieve_memory_records", **payload))

    def list_records(self, *, namespace_path: str) -> Sequence[MemoryRecord]:
        """Every record under a namespace prefix, page after page."""
        found: list[MemoryRecord] = []
        token = ""
        for _ in range(MAX_PAGES):
            payload = list_records_payload(namespace_path)
            if token:
                payload["nextToken"] = token
            answer = self._call(self._client, "list_memory_records", **payload)
            found.extend(records_of(answer))
            token = str(answer.get("nextToken") or "")
            if not token:
                break
        return tuple(found)

    def put_records(self, records: Sequence[MemoryRecord]) -> None:
        """Write an approved text verbatim, with ``BatchCreateMemoryRecords``."""
        if not records:
            return
        payload = batch_create_records_payload(records, strategy_id=self._strategy())
        self._call(self._client, "batch_create_memory_records", **payload)

    def delete_records(self, *, actor_id: str, session_id: str) -> int:
        """Forget one job: list its records, then delete them."""
        namespace = self.settings.episode_namespace(actor_id, session_id)
        identifiers = [
            record.record_id for record in self.list_records(namespace_path=namespace) if record.record_id
        ]
        if not identifiers:
            return 0
        self._call(self._client, "delete_memory_records", **delete_records_payload(identifiers))
        return len(identifiers)

    def reflections(self, *, actor_id: str) -> Sequence[Lesson]:
        """The lessons the service produced for one perimeter, and no other."""
        scope = scope_of_actor(actor_id)
        namespace = reflection_namespace(self.settings, scope)
        return tuple(
            lesson_of_record(scope, record) for record in self.list_records(namespace_path=namespace)
        )

    # -- plumbing ----------------------------------------------------------- #
    def _strategy(self) -> str:
        """The identifier of the episodic strategy, or its name before creation."""
        return self.strategy_id or self.settings.strategy_id or self.settings.strategy_name

    def _create_or_find(self, payload: dict, name: str) -> dict:
        """Create the memory, or read the one that already carries this name."""
        try:
            return self._call(self._control, "create_memory", **payload)
        except BackendUnavailable as error:
            if not _is_conflict(error):
                raise
            found = self._find_memory(name)
            if found is None:
                raise
            return found

    def _find_memory(self, name: str) -> dict | None:
        """The memory that already exists, read through the control plane."""
        try:
            listed = self._call(self._control, "list_memories")
        except BackendUnavailable:
            return None
        for summary in listed.get("memorySummaries") or []:
            if str(summary.get("name") or "") != name:
                continue
            memory_id = str(summary.get("memoryId") or "")
            if memory_id:
                return self._call(self._control, "get_memory", memoryId=memory_id)
        return None

    def _call(self, client: Any, operation: str, **kwargs: Any) -> dict:
        """One API call; any client error becomes ``BackendUnavailable``."""
        try:
            answer = getattr(client, operation)(**kwargs)
        except Exception as error:
            raise BackendUnavailable(
                f"AgentCore Memory : {operation} a echoue",
                operation=operation,
                cause=str(error),
                aws_code=_error_code(error),
            ) from error
        return answer if isinstance(answer, dict) else {}


def _boto3_client(service: str, settings: Settings) -> Any:
    """Build one boto3 client - the only place boto3 is imported."""
    import boto3

    return boto3.client(service, region_name=settings.region)


def _error_code(error: BaseException) -> str:
    """The error code of a client error, empty when the error carries none."""
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code") or "")
    return type(error).__name__


def _is_conflict(error: BackendUnavailable) -> bool:
    """True when the error says the memory already exists."""
    code = str(error.details.get("aws_code") or "").lower()
    return any(marker in code for marker in CONFLICT_CODES)


def strategy_id_of(answer: dict) -> str:
    """The identifier of the episodic strategy, wherever the answer puts it."""
    for strategy in answer.get("memoryStrategies") or []:
        episodic = strategy.get("episodicMemoryStrategy") or {}
        for key in ("strategyId", "memoryStrategyId", "id"):
            if episodic.get(key):
                return str(episodic[key])
    return ""


def lesson_of_record(scope: Scope, record: MemoryRecord) -> Lesson:
    """A record of the reflection space, read as the lesson it holds.

    The confidence is the one AgentCore Memory gives with each lesson
    (specification 13.5): it comes from the service, never from a model, and it
    is kept inside the 0.1 to 1.0 range the specification defines.
    """
    episode_ids = tuple(
        part.strip() for part in record.metadata.get("episodeIds", "").split(",") if part.strip()
    )
    return Lesson(
        lesson_id=record.record_id or lesson_id(scope.actor_id, *episode_ids),
        scope=scope,
        use_case=record.metadata.get("useCase") or record.content,
        confidence=_confidence_of(record),
        episode_ids=episode_ids,
        memory_record_id=record.record_id,
        level=Level.PROPOSED,
        created_at=record.created_at,
    )


def _confidence_of(record: MemoryRecord) -> float:
    """The generalisation confidence of a lesson, bounded to 0.1 .. 1.0."""
    try:
        value = float(record.metadata.get("confidence") or 0.5)
    except (TypeError, ValueError):
        return 0.5
    return round(min(1.0, max(0.1, value)), 2)


__all__ = ["AgentCoreMemoryBackend", "lesson_of_record", "strategy_id_of"]
