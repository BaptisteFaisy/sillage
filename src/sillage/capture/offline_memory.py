"""The offline memory backend the capture falls back on.

``sillage.memory`` is written in parallel and may not be merged yet: the
capture must still run offline, in the tests, in the demo and in the smoke.
This recorder is that fallback. It satisfies
``kernel.protocols.MemoryBackend`` - the very port the AgentCore backend
implements - so the day ``sillage.memory.local`` lands, it is swapped in
without a single line of the capture changing.

It is deliberately minimal and honest about it:

* it replays a trace as dated events, in batches of ``max_events_per_call``,
  and remembers the ``extraction_mode`` of each batch, which is how a test
  proves that a "do not keep" job is stored without being distilled (R4);
* it does not distil anything: ``reflections`` is empty, because a reflection
  is produced by the service, never invented by a fallback;
* its search is a word overlap, not a semantic one - enough to answer a
  demonstration, and never enough to be mistaken for AgentCore Memory.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sillage.capture.clock import SystemClock
from sillage.kernel.config import DEFAULT, Settings
from sillage.kernel.ids import make_id
from sillage.kernel.protocols import Clock, MemoryRecord
from sillage.kernel.types import ExtractionMode, Lesson, Trace

#: A word of a query or of a stored text, for the overlap score.
_WORD = re.compile(r"[a-z0-9à-ÿ]+")


@dataclass(frozen=True)
class StoredEvent:
    """One replayed event, as the offline recorder keeps it."""

    record_id: str
    namespace: str
    actor_id: str
    session_id: str
    role: str
    content: str
    kind: str
    at: datetime | None
    extraction_mode: ExtractionMode
    batch: int = 1


class OfflineMemoryBackend:
    """``MemoryBackend`` in memory: the capture's own offline fallback."""

    def __init__(self, settings: Settings | None = None, clock: Clock | None = None) -> None:
        self.settings = settings or DEFAULT
        self.clock = clock or SystemClock()
        self.memory_id = ""
        self.memory_name = ""
        self._events: list[StoredEvent] = []
        self._records: dict[str, MemoryRecord] = {}

    # -- MemoryBackend ----------------------------------------------------- #
    def ensure_memory(self, name: str, description: str) -> str:
        """Pretend to create the memory resource; return a stable id."""
        self.memory_name = name
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
        """Replay a normalised trace as dated events, in batches of 100."""
        namespace = self.namespace(actor_id, session_id)
        batches = self._batches(trace)
        for index, batch in enumerate(batches, start=1):
            self._store_batch(namespace, actor_id, session_id, batch, extraction_mode, index)
        return make_id("trace", actor_id, session_id, str(len(batches)))

    def retrieve(self, *, namespace: str, query: str, top_k: int = 5) -> Sequence[MemoryRecord]:
        """Word overlap search inside one namespace, best first."""
        wanted = _words(query)
        hits = [
            self._record(event, score=len(wanted & _words(event.content)))
            for event in self._events
            if event.namespace == namespace
        ]
        kept = [hit for hit in hits if hit.score > 0]
        kept.sort(key=lambda hit: (-hit.score, hit.record_id))
        return tuple(kept[:top_k])

    def list_records(self, *, namespace_path: str) -> Sequence[MemoryRecord]:
        """Every record under a namespace prefix, in the order they were written."""
        return tuple(
            self._record(event, score=0.0)
            for event in self._events
            if event.namespace.startswith(namespace_path)
        )

    def put_records(self, records: Sequence[MemoryRecord]) -> None:
        """Write approved text verbatim, under its own namespace."""
        for record in records:
            self._records[record.record_id] = record

    def delete_records(self, *, actor_id: str, session_id: str) -> int:
        """Forget one job; return how many records disappeared."""
        namespace = self.namespace(actor_id, session_id)
        before = len(self._events)
        self._events = [event for event in self._events if event.namespace != namespace]
        for record_id in list(self._records):
            if self._records[record_id].namespace == namespace:
                del self._records[record_id]
        return before - len(self._events)

    def reflections(self, *, actor_id: str) -> Sequence[Lesson]:
        """No lesson: a fallback never invents a reflection."""
        return ()

    # -- offline specific -------------------------------------------------- #
    def namespace(self, actor_id: str, session_id: str = "") -> str:
        """Namespace of one job, built by the kernel templates."""
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
        """Every record written verbatim through ``put_records``."""
        return tuple(self._records.values())

    def _batches(self, trace: Trace) -> list[tuple]:
        """The events of a trace, cut into batches the service accepts."""
        size = max(self.settings.max_events_per_call, 1)
        events = list(trace.events)
        return [tuple(events[index : index + size]) for index in range(0, len(events), size)]

    def _store_batch(
        self,
        namespace: str,
        actor_id: str,
        session_id: str,
        batch: Sequence,
        extraction_mode: ExtractionMode,
        index: int,
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
                )
            )

    def _record(self, event: StoredEvent, *, score: float) -> MemoryRecord:
        """The memory record shape of one stored event."""
        return MemoryRecord(
            record_id=event.record_id,
            namespace=event.namespace,
            content=event.content,
            score=score,
            metadata={
                "role": event.role,
                "kind": event.kind,
                "extractionMode": event.extraction_mode.value,
                "batch": str(event.batch),
            },
            created_at=event.at,
        )


def _words(text: str) -> frozenset[str]:
    """The lowercased words of a text, for the overlap score."""
    return frozenset(_WORD.findall((text or "").lower()))


__all__ = ["OfflineMemoryBackend", "StoredEvent"]
