"""Scores of the catalogue: rates, freshness, token budget, dashboard.

Every number here is computed by code, never by a model (ENF-IA-01), and no
figure is ever grouped by person (ENF-RH-01): the only keys the metrics expose
are levels, perimeters and technologies.

The three notes of chapter 13.5 stay separate on purpose - a success rate, a
freshness, a generalisation confidence - because a single blended score could
not be explained to a referent.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from math import ceil

from sillage.kernel.config import DEFAULT, Settings
from sillage.kernel.protocols import CatalogStore
from sillage.kernel.types import LEVEL_CODE, Fiche, Level, ServedFiche

#: Neutral rate of a fiche that has never been confirmed nor contradicted.
NEUTRAL_RATE = 0.5


def success_rate(successes: int = 0, failures: int = 0) -> float:
    """Success rate of a fiche, from its feedbacks only (specification 13.5).

    The formula is the one of the specification: ``(successes + 1) /
    (successes + failures + 2)``. It starts at the neutral ``0.5`` when there
    is no feedback at all, a single confirmation does not read as a certainty
    (``2/3``) and a perfect record stays below ``1.0`` (``6/7`` for five
    successes): a rate of ``1.0`` would claim a proof the usage cannot give.

    Usages without feedback never enter the rate (EF-RET-05): they are counted
    apart in ``Fiche.no_feedback_count`` and only ``successes`` and
    ``failures`` are used here.
    """
    ok = max(0, int(successes))
    ko = max(0, int(failures))
    return (ok + 1) / (ok + ko + 2)


def freshness_days(fiche: Fiche, now: datetime) -> int | None:
    """Days since the last confirmed success, ``None`` when never confirmed."""
    if fiche.last_confirmation is None:
        return None
    return max(0, _elapsed_days(now, fiche.last_confirmation))


def is_stale(fiche: Fiche, now: datetime, *, settings: Settings = DEFAULT) -> bool:
    """True when the fiche has not been confirmed for ``stale_days`` (180).

    A fiche that was never confirmed is not stale: there is nothing to age yet,
    and the guardian agent reports it separately (EF-ENT-01).
    """
    days = freshness_days(fiche, now)
    return days is not None and days >= settings.stale_days


def served(fiche: Fiche, now: datetime, settings: Settings = DEFAULT) -> ServedFiche:  # noqa: ARG001
    """A fiche as served by ``recall``: content plus the notes that back it.

    ``settings`` is part of the contract signature (CONTRACT.md 4) so callers
    can pass the thresholds of a demo; the served view itself only needs the
    counters of the fiche and the current date.
    """
    return ServedFiche(
        fiche=fiche,
        success_rate=success_rate(successes=fiche.success_count, failures=fiche.failure_count),
        freshness_days=freshness_days(fiche, now),
        generalisation_confidence=None,
    )


def estimate_tokens(text: str, settings: Settings = DEFAULT) -> int:
    """Rough token count of a text, used to stay under the budget (ENF-PERF-03).

    The estimate rounds up: an answer that fits according to this number also
    fits according to the real tokenizer, up to the margin of
    ``tokens_per_char``.
    """
    if not text:
        return 0
    return ceil(len(text) * settings.tokens_per_char)


def catalogue_metrics(catalog: CatalogStore, now: datetime) -> dict[str, object]:
    """Dashboard figures of the catalogue (EF-PIL-01 to EF-PIL-04).

    Episodes captured, fiches per level, reuse, average success rate and
    freshness. The keys are levels, perimeters and technologies: never a person
    (EF-PIL-04, ENF-RH-01).
    """
    fiches = tuple(catalog.list_fiches())
    used = [fiche for fiche in fiches if _was_used(fiche)]
    rates = [
        success_rate(successes=fiche.success_count, failures=fiche.failure_count)
        for fiche in fiches
        if fiche.success_count + fiche.failure_count > 0
    ]
    ages = [days for days in (freshness_days(fiche, now) for fiche in fiches) if days is not None]
    return {
        "generated_at": now.isoformat(),
        "episodes": len(tuple(catalog.list_episodes())),
        "clients": len(tuple(catalog.list_clients())),
        "fiches": len(fiches),
        "fiches_by_level": _by_level(fiches),
        "fiches_by_scope": _by_scope(fiches),
        "used_fiches": len(used),
        "reuse_rate": _ratio(len(used), len(fiches)),
        "usages_without_feedback": sum(fiche.no_feedback_count for fiche in fiches),
        "feedbacks": _feedback_count(catalog, fiches),
        "success_rate": _average(rates),
        "freshness_days": _average(ages),
        "stale_fiches": sum(1 for fiche in fiches if is_stale(fiche, now)),
    }


def _elapsed_days(later: datetime, earlier: datetime) -> int:
    """Whole days between two instants, naive datetimes read as UTC."""
    if later.tzinfo is None and earlier.tzinfo is not None:
        later = later.replace(tzinfo=UTC)
    if earlier.tzinfo is None and later.tzinfo is not None:
        earlier = earlier.replace(tzinfo=UTC)
    return (later - earlier).days


def _was_used(fiche: Fiche) -> bool:
    """A fiche was served at least once when any of its counters moved."""
    return fiche.success_count + fiche.failure_count + fiche.no_feedback_count > 0


def _by_level(fiches: Sequence[Fiche]) -> dict[str, int]:
    """Count of fiches per displayed level code, every level present."""
    counts = {LEVEL_CODE[level]: 0 for level in Level}
    for fiche in fiches:
        counts[LEVEL_CODE[fiche.level]] += 1
    return counts


def _by_scope(fiches: Sequence[Fiche]) -> dict[str, int]:
    """Count of fiches per perimeter: the actor is the perimeter, not a person."""
    counts: dict[str, int] = {}
    for fiche in fiches:
        key = fiche.scope.actor_id
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _feedback_count(catalog: CatalogStore, fiches: Sequence[Fiche]) -> int:
    """Number of feedbacks recorded, one per answered usage."""
    return sum(len(tuple(catalog.list_feedback(fiche.fiche_id))) for fiche in fiches)


def _ratio(part: int, total: int) -> float:
    return round(part / total, 4) if total else 0.0


def _average(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None
