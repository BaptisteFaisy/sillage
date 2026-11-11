"""Deterministic fingerprint of a capture, so a trace is processed once.

EF-CAP-11 and recipe R3: the same finished job can reach Sillage twice - a
ticket closed then reopened and closed again, an EventBridge rule that retries,
an operator who clicks twice. The capture must then produce **one** episode.

The key is built by the kernel (``kernel.ids.trace_fingerprint``) over a
canonical JSON of the trace: the same normalised work always gives the same
key, whatever the machine, the dictionary order or the hour of reception. Two
fields are deliberately left out, because they describe the capture and not the
work: the reception instant, and the bookkeeping attributes added after the
fact (object key, capture state, who asked not to keep it). Without that, a
re-capture of an already stored job would look like a new one.
"""

from __future__ import annotations

import json
from typing import Any

from sillage.kernel.ids import slug, trace_fingerprint
from sillage.kernel.protocols import CatalogStore
from sillage.kernel.types import Trace, TraceEvent

#: Origin keys that describe the capture, not the work: out of the fingerprint.
VOLATILE_ORIGIN_KEYS = frozenset({"rawKey", "captureState", "doNotKeepBy"})


def canonical_trace(trace: Trace) -> dict[str, Any]:
    """The trace as plain data, with every list sorted and every date in ISO."""
    return {
        "workItemId": trace.work_item_id,
        "scope": trace.scope.actor_id,
        "source": trace.source.value,
        "authorKind": trace.author_kind.value,
        "author": trace.author,
        "technologies": sorted(trace.technologies),
        "doNotDistill": trace.do_not_distill,
        "closedAt": trace.closed_at.isoformat() if trace.closed_at else "",
        "receivedAt": trace.received_at.isoformat() if trace.received_at else "",
        "origin": {
            key: value for key, value in sorted(trace.origin.items()) if key not in VOLATILE_ORIGIN_KEYS
        },
        "events": [canonical_event(event) for event in trace.events],
    }


def canonical_event(event: TraceEvent) -> dict[str, Any]:
    """One event of the canonical form: role, kind, author, date and text."""
    return {
        "role": event.role.value,
        "kind": event.kind,
        "author": event.author or "",
        "authorKind": event.author_kind.value,
        "at": event.at.isoformat() if event.at else "",
        "text": event.text,
    }


def canonical_json(trace: Trace) -> str:
    """The whole normalised trace as stable JSON: the raw copy of EF-CAP-13."""
    return _dumps(canonical_trace(trace))


def fingerprint(trace: Trace) -> str:
    """Deduplication key of one capture (EF-CAP-11).

    The reception instant is left out: it describes this capture, not the work,
    and a job received twice at two different hours is still one job (R3).
    """
    data = canonical_trace(trace)
    data.pop("receivedAt", None)
    return trace_fingerprint(trace.work_item_id, _dumps(data))


def _dumps(data: dict[str, Any]) -> str:
    """Stable bytes: keys sorted, no space, accents kept readable."""
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def is_duplicate(store: CatalogStore, trace: Trace) -> bool:
    """True when this exact capture was already received (recipe R3)."""
    return store.trace_seen(fingerprint(trace))


def remember(store: CatalogStore, trace: Trace) -> str:
    """Remember a capture and return its fingerprint."""
    key = fingerprint(trace)
    store.mark_trace_seen(key, trace.work_item_id)
    return key


def raw_key(trace: Trace) -> str:
    """Object key of the raw copy of a trace, in the versioned store (EF-CAP-13).

    Built from the perimeter and the work item, so a re-capture of the same job
    writes a **new version of the same key** instead of a second object: the
    audit trail stays one line per job, and a deletion erases every version.
    """
    return f"traces/{trace.scope.actor_id}/{slug(trace.work_item_id)}.json"


__all__ = [
    "VOLATILE_ORIGIN_KEYS",
    "canonical_event",
    "canonical_json",
    "canonical_trace",
    "fingerprint",
    "is_duplicate",
    "raw_key",
    "remember",
]
