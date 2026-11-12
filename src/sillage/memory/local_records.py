"""What the offline backend stores, and how a trace is cut into batches.

Kept apart from :mod:`sillage.memory.local` so that neither file grows past the
limit of 500 lines, and so the shapes below can be reused: the AgentCore backend
sends the very same batches, the very same client tokens and the very same
metadata, only over the network instead of in a dictionary.

``StoredEvent`` carries the fields of ``capture.offline_memory.StoredEvent`` in
the same order, plus the client token of its batch: a test written against the
capture's fallback reads the same attributes here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sillage.kernel.protocols import MemoryRecord
from sillage.kernel.types import ExtractionMode, Trace, TraceEvent

#: The ``kind`` of a record the service itself distilled from a trace.
KIND_EPISODE = "episode"
#: The ``kind`` of a record whose text was approved by a referent (12.4).
KIND_APPROVED = "approved"

#: The metadata keys of a distilled episode record.
EPISODE_METADATA_KEYS = (
    "kind",
    "scope",
    "workItemId",
    "techno",
    "source",
    "auteurType",
    "situation",
    "intent",
    "outcome",
    "teachings",
    "pitfalls",
    "clientToken",
)

#: Separator of a list kept in one metadata value: metadata are strings only.
LIST_SEPARATOR = " | "


@dataclass(frozen=True)
class StoredEvent:
    """One replayed event, as the offline backend keeps it."""

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
    client_token: str = ""


def batches_of(trace: Trace, size: int) -> tuple[tuple[TraceEvent, ...], ...]:
    """The events of a trace, cut in batches the service accepts.

    ``CreateEvent`` carries at most 100 conversational elements per call
    (specification annex B); a longer trace is replayed in several calls, each
    with its own stable client token, so a retry never duplicates an event.
    """
    limit = max(int(size), 1)
    events = tuple(trace.events)
    if not events:
        return ()
    return tuple(events[index : index + limit] for index in range(0, len(events), limit))


def client_token(session_id: str, index: int) -> str:
    """The idempotency token of one batch: ``<session>-part-<n>``, 1 based."""
    return f"{session_id}-part-{index}"


def bounded_metadata(values: Mapping[str, str], limit: int) -> dict[str, str]:
    """The first ``limit`` metadata, in their own order (15 keys maximum, 12.6)."""
    kept: dict[str, str] = {}
    for key, value in values.items():
        if len(kept) >= max(int(limit), 0):
            break
        kept[key] = str(value)
    return kept


def event_metadata(trace: Trace, limit: int) -> dict[str, str]:
    """The metadata of the events of one trace, bounded by the service limit."""
    return bounded_metadata(trace.metadata, limit)


def episode_metadata(
    trace: Trace,
    *,
    situation: str,
    intent: str,
    outcome: str,
    teachings: Sequence[str] = (),
    pitfalls: Sequence[str] = (),
    token: str = "",
) -> dict[str, str]:
    """The metadata of a distilled episode record, read back by the reflection.

    Everything a reflection needs is here as text: the perimeter, the technology,
    the intention the episode is indexed on, its verdict and - kept apart, as
    EF-DIS-04 requires - the paths that were tried without success.
    """
    return {
        "kind": KIND_EPISODE,
        "scope": trace.scope.actor_id,
        "workItemId": trace.work_item_id,
        "techno": ",".join(trace.technologies[:4]),
        "source": trace.source.value,
        "auteurType": trace.author_kind.value,
        "situation": situation,
        "intent": intent,
        "outcome": outcome,
        "teachings": LIST_SEPARATOR.join(teachings),
        "pitfalls": LIST_SEPARATOR.join(pitfalls),
        "clientToken": token,
    }


def episode_record(
    *,
    record_id: str,
    namespace: str,
    content: str,
    metadata: Mapping[str, str],
    created_at: datetime | None = None,
) -> MemoryRecord:
    """The memory record of one distilled episode."""
    return MemoryRecord(
        record_id=record_id,
        namespace=namespace,
        content=content,
        score=0.0,
        metadata=dict(metadata),
        created_at=created_at,
    )


def split_list(value: str) -> tuple[str, ...]:
    """Read back a list kept in one metadata value."""
    return tuple(part.strip() for part in (value or "").split(LIST_SEPARATOR) if part.strip())


__all__ = [
    "EPISODE_METADATA_KEYS",
    "KIND_APPROVED",
    "KIND_EPISODE",
    "LIST_SEPARATOR",
    "StoredEvent",
    "batches_of",
    "bounded_metadata",
    "client_token",
    "episode_metadata",
    "episode_record",
    "event_metadata",
    "split_list",
]
