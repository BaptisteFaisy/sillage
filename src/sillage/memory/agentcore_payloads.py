"""The exact request shapes of AgentCore Memory: annexes A, B and C.

Every payload the deployed backend sends is built here, as a plain dictionary,
by a pure function. Two reasons:

* the shapes are the ones of the specification - annex A for ``CreateMemory``,
  annex B for ``CreateEvent``, annex C for ``RetrieveMemoryRecords`` - and a test
  can compare the dictionary it gets with the annex, character by character;
* the namespace templates come from :mod:`sillage.kernel.config`, so the offline
  spaces and the deployed ones cannot drift apart.

Nothing here imports boto3 or touches the network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from sillage.kernel.config import EPISODE_NAMESPACE, REFLECTION_NAMESPACE, Settings
from sillage.kernel.protocols import MemoryRecord
from sillage.kernel.types import Trace, TraceEvent
from sillage.memory.local_records import bounded_metadata, client_token

#: One ``CreateEvent`` call carries at most this many conversational elements.
MAX_ELEMENTS_PER_CALL = 100


def create_memory_payload(settings: Settings) -> dict:
    """Annex A: the ``CreateMemory`` request, field by field."""
    return {
        "name": settings.memory_name,
        "description": settings.memory_description,
        "eventExpiryDuration": settings.event_expiry_days,
        "memoryStrategies": [
            {
                "episodicMemoryStrategy": {
                    "name": settings.strategy_name,
                    "description": settings.strategy_description,
                    "namespaceTemplates": [EPISODE_NAMESPACE],
                    "reflectionConfiguration": {"namespaceTemplates": [REFLECTION_NAMESPACE]},
                    "recordExpirySpecification": {"recordExpiryDurationDays": settings.record_expiry_days},
                }
            }
        ],
    }


def create_event_payload(
    *,
    actor_id: str,
    session_id: str,
    batch: Sequence[TraceEvent],
    metadata: Mapping[str, str],
    timestamp: int,
    index: int,
    extraction_mode: str = "",
) -> dict:
    """Annex B: one ``CreateEvent`` request, with a stable client token.

    ``extraction_mode`` is added only when it is not the default: a job its author
    asked not to keep is replayed with ``SKIP``, so it is stored and never
    distilled (specification 13.1, EF-CAP-12, R4).
    """
    payload: dict = {
        "actorId": actor_id,
        "sessionId": session_id,
        "eventTimestamp": timestamp,
        "clientToken": client_token(session_id, index),
        "metadata": string_values(metadata),
        "payload": conversational_payload(batch),
    }
    if extraction_mode:
        payload["extractionMode"] = extraction_mode
    return payload


def string_values(metadata: Mapping[str, str]) -> dict[str, dict[str, str]]:
    """The metadata shape of annex B: every value inside ``{"stringValue": ...}``."""
    return {key: {"stringValue": str(value)} for key, value in metadata.items()}


def conversational_payload(events: Sequence[TraceEvent]) -> list[dict]:
    """The payload of annex B: one conversational element per event."""
    return [
        {"conversational": {"role": event.role.value, "content": {"text": event.text}}} for event in events
    ]


def retrieve_payload(*, namespace: str, query: str, top_k: int, strategy_id: str) -> dict:
    """Annex C: the ``RetrieveMemoryRecords`` request."""
    return {
        "namespace": namespace,
        "maxResults": top_k,
        "searchCriteria": {
            "memoryStrategyId": strategy_id,
            "searchQuery": query,
            "topK": top_k,
        },
    }


def batch_create_records_payload(records: Sequence[MemoryRecord], *, strategy_id: str = "") -> dict:
    """The ``BatchCreateMemoryRecords`` request: the approved text, verbatim."""
    return {"records": [record_payload(record, strategy_id=strategy_id) for record in records]}


def record_payload(record: MemoryRecord, *, strategy_id: str = "") -> dict:
    """One record of a batch write. The text is written as it was approved."""
    payload: dict = {"namespace": record.namespace, "content": {"text": record.content}}
    if strategy_id:
        payload["memoryStrategyId"] = strategy_id
    return payload


def list_records_payload(namespace_path: str, *, max_results: int = 100) -> dict:
    """The ``ListMemoryRecords`` request: walk everything under a prefix."""
    return {"namespacePath": namespace_path, "maxResults": max_results}


def delete_records_payload(record_ids: Sequence[str]) -> dict:
    """The ``DeleteMemoryRecords`` request."""
    return {"recordIds": [str(record_id) for record_id in record_ids]}


def event_metadata_payload(trace: Trace, settings: Settings) -> dict[str, str]:
    """The metadata of the events of a trace, bounded by the service limit."""
    return bounded_metadata(trace.metadata, settings.max_event_metadata)


def event_timestamp(batch: Sequence[TraceEvent], trace: Trace, fallback: datetime) -> int:
    """The epoch seconds of one batch: the date of its first dated element."""
    for event in batch:
        if event.at is not None:
            return _epoch(event.at)
    for moment in (trace.closed_at, trace.received_at):
        if moment is not None:
            return _epoch(moment)
    return _epoch(fallback)


def _epoch(moment: datetime) -> int:
    """Epoch seconds of an instant, a naive one read as UTC."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return int(aware.timestamp())


def records_of(response: Mapping | None) -> tuple[MemoryRecord, ...]:
    """Read the records of a response, whatever shape the service answered with."""
    summaries = (response or {}).get("memoryRecordSummaries") or []
    return tuple(_record_of(summary) for summary in summaries)


def _record_of(summary: Mapping) -> MemoryRecord:
    """One ``memoryRecordSummary`` as a kernel record."""
    return MemoryRecord(
        record_id=str(summary.get("recordId") or ""),
        namespace=str(summary.get("namespace") or ""),
        content=_text_of(summary.get("content")),
        score=float(summary.get("score") or 0.0),
        metadata=_metadata_of(summary.get("metadata")),
        created_at=_moment_of(summary.get("createdAt")),
    )


def _text_of(content: object) -> str:
    """The text of a record: a ``{"text": ...}`` mapping or a plain string."""
    if isinstance(content, Mapping):
        return str(content.get("text") or "")
    return str(content or "")


def _metadata_of(metadata: object) -> dict[str, str]:
    """The metadata of a record, read back from the ``stringValue`` shape."""
    if not isinstance(metadata, Mapping):
        return {}
    found: dict[str, str] = {}
    for key, value in metadata.items():
        if isinstance(value, Mapping):
            found[str(key)] = str(value.get("stringValue") or "")
        else:
            found[str(key)] = str(value)
    return found


def _moment_of(value: object) -> datetime | None:
    """The date of a record: a datetime, epoch seconds, or nothing."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    return None


__all__ = [
    "MAX_ELEMENTS_PER_CALL",
    "batch_create_records_payload",
    "create_event_payload",
    "create_memory_payload",
    "delete_records_payload",
    "event_metadata_payload",
    "event_timestamp",
    "list_records_payload",
    "record_payload",
    "records_of",
    "retrieve_payload",
    "string_values",
]
