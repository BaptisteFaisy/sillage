"""The offline memory backend: AgentCore Memory, without the service.

One object, the standard library, no network, no model - and every answer
reproducible, so a test that passes here passes the same way twice.

It replaces :class:`sillage.capture.offline_memory.OfflineMemoryBackend` in the
real wiring (``capture.ingest.default_memory`` prefers this module as soon as it
exists) and it does more than the fallback, because the fallback deliberately
stays minimal:

* a trace replayed as events is **distilled** into one episode record, the way
  the episodic strategy does server side, unless the job was marked "do not
  keep" - then it is stored and never distilled (EF-CAP-12, R4);
* the search is a tf-idf cosine over the record contents
  (:mod:`sillage.memory.tfidf`), which is what makes an intention find the
  episodes that answer it (specification 13.4);
* :meth:`reflections` groups the episodes of **one** perimeter by technology
  family and similarity, and returns the lessons - never a lesson that crosses
  two clients (EF-DIS-02).

The namespaces come from the templates of :mod:`sillage.kernel.config`, so the
offline spaces and the deployed ones are the same strings.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import UTC, datetime

from sillage.kernel.config import DEFAULT, Settings
from sillage.kernel.ids import episode_id, make_id
from sillage.kernel.protocols import Clock, MemoryRecord
from sillage.kernel.types import ExtractionMode, Lesson, Trace, TraceEvent
from sillage.memory.distill import episode_content, episode_metadata_of, lesson_from_records
from sillage.memory.local_records import (
    KIND_EPISODE,
    StoredEvent,
    batches_of,
    client_token,
    episode_record,
)
from sillage.memory.namespaces import scope_of_actor
from sillage.memory.tfidf import SemanticIndex

#: Sort key of a record that carries no date, so an incomplete record still sorts.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class LocalMemoryBackend:
    """``MemoryBackend`` in memory: the offline twin of AgentCore Memory."""

    def __init__(self, settings: Settings = DEFAULT, clock: Clock | None = None) -> None:
        self.settings = settings
        self.clock = clock
        self.memory_id = ""
        self.memory_name = ""
        self.memory_description = ""
        self._events: list[StoredEvent] = []
        self._records: dict[str, MemoryRecord] = {}
        self._index = SemanticIndex()

    # -- MemoryBackend ------------------------------------------------------ #
    def ensure_memory(self, name: str, description: str) -> str:
        """Pretend to create the memory resource; return a stable id."""
        self.memory_name = name
        self.memory_description = description
        self.memory_id = self.memory_id or make_id("memory", name, description)
        return self.memory_id

    def create_events(
        self,
        *,
        actor_id: str,
        session_id: str,
        trace: Trace,
        extraction_mode: ExtractionMode = ExtractionMode.DEFAULT,
    ) -> str:
        """Replay a trace as dated events, then distil one episode per job.

        The events go in batches of ``max_events_per_call`` - 100, the limit of
        ``CreateEvent`` - each batch with its own stable client token, so a retry
        replays nothing twice.
        """
        namespace = self.namespace(actor_id, session_id)
        batches = batches_of(trace, self.settings.max_events_per_call)
        for index, batch in enumerate(batches, start=1):
            token = client_token(session_id, index)
            self._store_batch(namespace, actor_id, session_id, batch, extraction_mode, index, token)
            if index == 1 and extraction_mode is not ExtractionMode.SKIP:
                self._distil(actor_id, namespace, trace, token)
        return make_id("trace", actor_id, session_id, str(len(batches)))

    def retrieve(self, *, namespace: str, query: str, top_k: int = 5) -> Sequence[MemoryRecord]:
        """The records closest to an intention, inside one namespace.

        The namespace of a strategy covers the sessions under it, so a search on
        ``/strategy/<id>/actor/client-a/`` reads the episodes and the lessons of
        that client and never leaves it (specification annex C).
        """
        candidates = [
            record_id for record_id, record in self._records.items() if record.namespace.startswith(namespace)
        ]
        scored = self._index.search(query, top_k=top_k, within=candidates)
        return tuple(replace(self._records[record_id], score=round(score, 6)) for record_id, score in scored)

    def list_records(self, *, namespace_path: str) -> Sequence[MemoryRecord]:
        """Every record under a namespace prefix, oldest first."""
        return self._sorted(self._under(namespace_path))

    def put_records(self, records: Sequence[MemoryRecord]) -> None:
        """Write an approved text verbatim: not one character is changed."""
        for record in records:
            self._records[record.record_id] = record
            self._index.index(record.record_id, record.content)

    def delete_records(self, *, actor_id: str, session_id: str) -> int:
        """Forget one job: its replayed events and its records.

        The answer is the number of stored elements that disappeared, so a caller
        can tell a job that was forgotten from a job that was never there
        (EF-ENT-03, R19).
        """
        namespace = self.namespace(actor_id, session_id)
        kept = [event for event in self._events if event.namespace != namespace]
        forgotten = [
            record_id for record_id in self._records if self._records[record_id].namespace == namespace
        ]
        removed = len(self._events) - len(kept)
        self._events = kept
        for record_id in forgotten:
            del self._records[record_id]
            self._index.remove(record_id)
        return removed + len(forgotten)

    def reflections(self, *, actor_id: str) -> Sequence[Lesson]:
        """The lessons the episodes of **one** perimeter teach (EF-DIS-02)."""
        scope = scope_of_actor(actor_id)
        records = tuple(
            record
            for record in self._sorted(self._records.values())
            if record.metadata.get("kind") == KIND_EPISODE and record.metadata.get("scope") == actor_id
        )
        return lesson_from_records(scope, records)

    # -- offline specific --------------------------------------------------- #
    def namespace(self, actor_id: str, session_id: str = "") -> str:
        """The namespace of one job, built by the templates of the kernel."""
        return self.settings.episode_namespace(actor_id, session_id)

    def events(
        self, *, session_id: str = "", extraction_mode: ExtractionMode | None = None
    ) -> tuple[StoredEvent, ...]:
        """The replayed events, for a test that wants to look at them."""
        return tuple(
            event
            for event in self._events
            if (not session_id or event.session_id == session_id)
            and (extraction_mode is None or event.extraction_mode is extraction_mode)
        )

    def records(self) -> tuple[MemoryRecord, ...]:
        """Every record held by the backend, distilled or written verbatim."""
        return self._sorted(self._records.values())

    def record(self, record_id: str) -> MemoryRecord | None:
        """One record by its id, ``None`` when the backend does not hold it."""
        return self._records.get(record_id)

    def now(self) -> datetime:
        """The current instant: the injected clock when there is one."""
        if self.clock is not None:
            return self.clock.now()
        return datetime.now(UTC)

    # -- internals ---------------------------------------------------------- #
    def _under(self, namespace_path: str) -> list[MemoryRecord]:
        """The records whose namespace starts with a prefix."""
        return [record for record in self._records.values() if record.namespace.startswith(namespace_path)]

    def _sorted(self, records: Iterable[MemoryRecord]) -> tuple[MemoryRecord, ...]:
        """A stable order: by date, then by id, never by insertion."""
        return tuple(sorted(records, key=lambda record: (record.created_at or _EPOCH, record.record_id)))

    def _store_batch(
        self,
        namespace: str,
        actor_id: str,
        session_id: str,
        batch: Sequence[TraceEvent],
        extraction_mode: ExtractionMode,
        index: int,
        token: str,
    ) -> None:
        """Keep one batch of events, each with its own record id."""
        for position, event in enumerate(batch):
            self._events.append(
                StoredEvent(
                    record_id=make_id(
                        "trace", actor_id, session_id, str(index), str(position), event.text[:32]
                    ),
                    namespace=namespace,
                    actor_id=actor_id,
                    session_id=session_id,
                    role=event.role.value,
                    content=event.text,
                    kind=event.kind,
                    at=event.at,
                    extraction_mode=extraction_mode,
                    batch=index,
                    client_token=token,
                )
            )

    def _distil(self, actor_id: str, namespace: str, trace: Trace, token: str) -> None:
        """What the episodic strategy does server side: one episode per job.

        The record id is the canonical episode id of the kernel, so the row the
        catalogue holds and the record the memory holds are the same object seen
        from two sides (EF-DIS-01, EF-DIS-05).
        """
        content = episode_content(trace)
        record_id = episode_id(actor_id, trace.work_item_id)
        self._records[record_id] = episode_record(
            record_id=record_id,
            namespace=namespace,
            content=content,
            metadata=episode_metadata_of(trace, token=token),
            created_at=trace.closed_at or self.now(),
        )
        self._index.index(record_id, content)


__all__ = ["LocalMemoryBackend"]
