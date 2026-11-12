"""The generated history file: how it is read, and what to do without it.

The ``data`` lot writes the synthetic history of the demonstration to
``data/generated/history.json`` (60 to 100 episodes, eight fault families). This
module reads it, and - when it is absent, empty or unreadable - falls back on the
deterministic history of :mod:`sillage.demo.history`.

**Nothing here may fail because ``data/`` is empty** (CONTRACT.md rule 7): a
missing file, an unreadable file, a malformed entry, an unknown capture source -
each of them degrades to the built-in history and the counters say which source
was used. The demo always has something to show.

The file shape is read tolerantly on purpose: a list of episodes, or a mapping
holding one under a known key, and each episode carries its own capture source.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sillage.capture.sources import trace_from_payload
from sillage.demo.history import FAMILIES, fallback_payloads
from sillage.kernel.types import Source, Trace

#: The generated history of the ``data`` lot, when it exists.
HISTORY_PATH = Path(__file__).resolve().parents[3] / "data" / "generated" / "history.json"

#: The label of the built-in history, shown when the generated file is absent.
FALLBACK_SOURCE = "historique de repli (demo/history.py)"

#: The keys a generated history file may hold its episodes under.
HISTORY_KEYS: tuple[str, ...] = ("history", "episodes", "traces", "tickets", "items")


def history_payloads(stack: Any, path: str | Path | None = None) -> tuple[tuple[dict[str, Any], ...], str]:
    """The episodes to load, and where they come from.

    The generated file wins when it exists and is readable; the built-in history
    answers otherwise. An empty ``data/`` directory is a normal state, not an
    error.
    """
    found = Path(path) if path is not None else HISTORY_PATH
    items = _read_items(found)
    if items:
        return items, str(found)
    client_ids = tuple(client.client_id for client in stack.clients())
    return fallback_payloads(client_ids or ("a", "b")), FALLBACK_SOURCE


def families_of(payloads: Sequence[Mapping[str, Any]]) -> int:
    """How many fault families the history covers."""
    codes = {str(payload.get("code") or payload.get("family") or "") for payload in payloads}
    codes.discard("")
    return len(codes) or len(FAMILIES)


def traces_of(stack: Any, payloads: Sequence[Mapping[str, Any]]) -> list[Trace]:
    """The traces of the history entries; an unreadable entry is skipped."""
    traces: list[Trace] = []
    for payload in payloads:
        try:
            traces.append(trace_from_payload(_source_of(payload), payload, stack.model))
        except Exception:  # one broken entry never breaks the demo
            continue
    return traces


def _read_items(found: Path) -> tuple[dict[str, Any], ...]:
    """The episodes of a generated history file; empty when it cannot be read."""
    try:
        raw = json.loads(found.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    items = _items_of(raw)
    return tuple(item for item in items if isinstance(item, Mapping))


def _items_of(raw: Any) -> list[Any]:
    """The list of episodes of a history file, whatever shape it has."""
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, Mapping):
        for key in HISTORY_KEYS:
            value = raw.get(key)
            if isinstance(value, list):
                return list(value)
    return []


def _source_of(payload: Mapping[str, Any]) -> Source:
    """The capture source of a history entry; anything unknown reads as Guichet."""
    try:
        return Source(str(payload.get("source", Source.GUICHET.value)))
    except ValueError:
        return Source.GUICHET


__all__ = [
    "FALLBACK_SOURCE",
    "HISTORY_KEYS",
    "HISTORY_PATH",
    "families_of",
    "history_payloads",
    "traces_of",
]
