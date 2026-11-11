"""Trust levels of a knowledge object: the state machine of chapter 10.2.

Every function here is pure: the counts decide, never a model (ENF-IA-01) and
never a person (ENF-RH-01). A fiche climbs the ladder only when the usage says
so, and it falls to "a l'ecart" when two negative feedbacks follow each other
(EF-VAL-06). The referent alone validates or withdraws: the code only counts.

Transitions
-----------
The diagram of chapter 10.2 is the reference. One arrow is added to it:
``proposee -> a l'ecart``. EF-VAL-06 states the rule ("two consecutive negative
feedbacks set the fiche aside") without any level precondition, and a proposed
fiche is already served to humans, so it can collect negative feedback too.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from sillage.kernel.config import DEFAULT, Settings
from sillage.kernel.types import FeedbackResult, Fiche, Level

#: Allowed level changes, straight from the diagram of chapter 10.2.
TRANSITIONS: dict[Level, tuple[Level, ...]] = {
    Level.RAW: (Level.PROPOSED,),
    Level.PROPOSED: (Level.VALIDATED, Level.SET_ASIDE, Level.WITHDRAWN),
    Level.VALIDATED: (Level.PROVEN, Level.SET_ASIDE),
    Level.PROVEN: (Level.SET_ASIDE,),
    Level.SET_ASIDE: (Level.VALIDATED, Level.WITHDRAWN),
    Level.WITHDRAWN: (),
}

#: Reason written on a fiche set aside by two consecutive negative feedbacks.
SET_ASIDE_REASON = "Deux retours negatifs consecutifs"


def _as_level(value: Level | str) -> Level:
    """Accept a level or its code and return the level."""
    if isinstance(value, Level):
        return value
    return Level(value)


def _author_count(authors: int | Sequence[str]) -> int:
    """Number of distinct authors, from a count or from the author list."""
    if isinstance(authors, int):
        return max(0, authors)
    return len({author for author in authors if author})


def can_transition(current: Level, target: Level) -> bool:
    """True when the state machine allows ``current -> target``.

    A level never transitions to itself: staying put is not a transition.
    """
    return _as_level(target) in TRANSITIONS.get(_as_level(current), ())


def next_level_after_successes(
    *,
    level: Level | str,
    successes: int,
    authors: int | Sequence[str],
    settings: Settings = DEFAULT,
) -> Level:
    """Level reached after ``successes`` confirmations by ``authors``.

    Only one promotion is automatic: a validated fiche becomes proven after
    ``proven_successes`` successes by at least ``proven_distinct_authors``
    distinct authors (EF-VAL-05). Every other level is returned unchanged:
    validation stays the decision of a referent, and a fiche set aside or
    withdrawn is never resurrected by usage alone.
    """
    current = _as_level(level)
    if current is not Level.VALIDATED:
        return current
    enough = successes >= settings.proven_successes
    shared = _author_count(authors) >= settings.proven_distinct_authors
    return Level.PROVEN if enough and shared else current


def should_set_aside(*, consecutive_failures: int, settings: Settings = DEFAULT) -> bool:
    """True when the fiche must be set aside (EF-VAL-06)."""
    return consecutive_failures >= settings.set_aside_consecutive_failures


def apply_feedback(
    fiche: Fiche,
    result: FeedbackResult,
    *,
    settings: Settings = DEFAULT,
    now: datetime | None = None,
    author: str = "",
) -> Fiche:
    """Apply one usage feedback to a fiche and return the new fiche.

    The input fiche is never modified: a new one is returned, so the caller
    decides when to write it. A positive feedback resets the failure counter
    and confirms the fiche; a negative one increases it, and two in a row set
    the fiche aside (the caller then warns the referent).

    ``now`` is the moment of the confirmation; without it the previous
    confirmation date is kept, the function stays pure. ``author`` is the
    pseudonym of the consumer: it feeds the distinct authors count of a proven
    promotion (EF-VAL-05) and is never stored in a metric (ENF-RH-01).
    """
    if result is FeedbackResult.HELPED:
        return _apply_success(fiche, settings=settings, now=now, author=author)
    return _apply_failure(fiche, settings=settings)


def _apply_success(fiche: Fiche, *, settings: Settings, now: datetime | None, author: str) -> Fiche:
    """Positive feedback: the fiche is confirmed, the failure counter is cleared."""
    authors = fiche.distinct_authors
    if author and author not in authors:
        authors = (*authors, author)
    successes = fiche.success_count + 1
    promoted = next_level_after_successes(
        level=fiche.level, successes=successes, authors=authors, settings=settings
    )
    level = promoted if can_transition(fiche.level, promoted) else fiche.level
    return replace(
        fiche,
        level=level,
        success_count=successes,
        consecutive_failures=0,
        no_feedback_count=max(0, fiche.no_feedback_count - 1),
        distinct_authors=authors,
        last_confirmation=now if now is not None else fiche.last_confirmation,
    )


def _apply_failure(fiche: Fiche, *, settings: Settings) -> Fiche:
    """Negative feedback: the failure counter grows, twice in a row sets aside."""
    consecutive = fiche.consecutive_failures + 1
    level = fiche.level
    reason = fiche.set_aside_reason
    if should_set_aside(consecutive_failures=consecutive, settings=settings) and can_transition(
        level, Level.SET_ASIDE
    ):
        level = Level.SET_ASIDE
        reason = SET_ASIDE_REASON
    return replace(
        fiche,
        level=level,
        failure_count=fiche.failure_count + 1,
        consecutive_failures=consecutive,
        no_feedback_count=max(0, fiche.no_feedback_count - 1),
        set_aside_reason=reason,
    )
