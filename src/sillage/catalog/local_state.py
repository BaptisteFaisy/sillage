"""In-memory state of the offline catalogue, and the ordering rules.

One dictionary per family of chapter 14, keyed the way the specification keys
them: a fiche is keyed by ``(fiche_id, version)`` so no correction can overwrite
its predecessor, and the fingerprint index makes capture idempotent (EF-CAP-11).

Every listing is sorted by ``(date, id)``: two runs of the demo, of the portal or
of a test give the same order, because nothing here may depend on insertion order
or on a hash seed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime

from sillage.kernel.protocols import Clock
from sillage.kernel.types import Client, Episode, Feedback, Fiche, Lesson, Scope, Trace, Usage

#: Sort key of a row: its date, then its id so ties never depend on insertion.
RowKey = tuple[datetime, str]

#: Date used when a row carries none, so an incomplete row still sorts.
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class CatalogState:
    """The dictionaries and the clock shared by every family of the catalogue."""

    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock
        self.clients: dict[str, Client] = {}
        self.traces: dict[str, Trace] = {}
        self.fingerprints: dict[str, str] = {}
        self.episodes: dict[str, Episode] = {}
        self.lessons: dict[str, Lesson] = {}
        self.fiches: dict[tuple[str, int], Fiche] = {}
        self.usages: dict[str, Usage] = {}
        self.feedbacks: list[Feedback] = []
        # Fiches kept elsewhere whose sources lost their work item (EF-ENT-03).
        self.flagged: tuple[Fiche, ...] = ()

    def now(self) -> datetime:
        """Current instant: the injected clock when there is one, UTC otherwise."""
        if self.clock is not None:
            return self.clock.now()
        return datetime.now(UTC)

    def latest_fiches(self) -> tuple[Fiche, ...]:
        """The current version of every fiche, sorted by perimeter then id."""
        current: dict[str, int] = {}
        for fiche_id, version in self.fiches:
            current[fiche_id] = max(version, current.get(fiche_id, 0))
        rows = [self.fiches[(fiche_id, version)] for fiche_id, version in current.items()]
        return tuple(sorted(rows, key=lambda fiche: (fiche.scope.actor_id, fiche.fiche_id)))

    def versions_of(self, fiche_id: str) -> tuple[Fiche, ...]:
        """Every kept version of one fiche, oldest first (EF-ENT-04)."""
        numbers = sorted(version for (kept, version) in self.fiches if kept == fiche_id)
        return tuple(self.fiches[(fiche_id, number)] for number in numbers)


def row_key(at: datetime | None, ident: str) -> RowKey:
    """Sort key of a row: its date (epoch when missing) then its id."""
    return (at or EPOCH, ident)


def newest_first[T](rows: Iterable[T], key: Callable[[T], RowKey], limit: int = 0) -> tuple[T, ...]:
    """Rows from the most recent to the oldest, at most ``limit`` of them."""
    ordered = sorted(rows, key=key, reverse=True)
    return tuple(ordered[:limit]) if limit > 0 else tuple(ordered)


def oldest_first[T](rows: Iterable[T], key: Callable[[T], RowKey]) -> tuple[T, ...]:
    """Rows from the oldest to the most recent."""
    return tuple(sorted(rows, key=key))


def filter_scope[T](rows: Sequence[T], scope: Scope | None) -> tuple[T, ...]:
    """Rows of one perimeter, or every row when no perimeter is asked for."""
    if scope is None:
        return tuple(rows)
    return tuple(row for row in rows if getattr(row, "scope", None) == scope)


def latest_per_fiche(fiches: Sequence[Fiche]) -> list[Fiche]:
    """Keep the latest version of each fiche, in order of first appearance.

    Both stores list a fiche once, at its current version: a catalogue screen that
    showed ``f-1`` three times would make a referent believe there are three
    fiches. The history stays reachable through ``get_fiche(id, version)``.
    """
    seen: dict[str, Fiche] = {}
    for fiche in fiches:
        kept = seen.get(fiche.fiche_id)
        if kept is None or fiche.version > kept.version:
            seen[fiche.fiche_id] = fiche
    return list(seen.values())


__all__ = [
    "EPOCH",
    "CatalogState",
    "RowKey",
    "filter_scope",
    "latest_per_fiche",
    "newest_first",
    "oldest_first",
    "row_key",
]
