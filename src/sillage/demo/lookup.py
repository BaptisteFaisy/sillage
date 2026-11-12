"""One consultation of the memory, shared by the try page and the demo.

The tools of CONTRACT.md §7 are another lot's job; until they are merged, the
public try page and the narrated demonstration still need to ask the memory the
same question and read the same answer. This module is that one implementation:
:func:`consult` runs the search of chapter 13.4, joins the hits with the catalogue
and returns the views the screens show.

Three rules of the specification are applied here and nowhere else in the demo:

* the perimeter of the caller comes first, the common knowledge after
  (EF-RES-04) - so the two are searched **separately** and merged, exactly as the
  contract says a caller does;
* an autonomous agent never receives a level outside
  ``SERVED_TO_AUTONOMOUS_AGENT`` (EF-RES-05), and is told how many elements were
  withheld;
* every figure shown - level, success rate, freshness - is read from the
  catalogue, computed by :mod:`sillage.catalog.scoring`, never by a model
  (ENF-IA-01).

When the ``tools`` lot lands, :func:`consult` becomes a thin wrapper around
``tools.recall``; the views below stay the shape the screens are written against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sillage.catalog.scoring import served
from sillage.kernel.types import (
    LEVEL_CODE,
    SERVED_TO_AUTONOMOUS_AGENT,
    SERVED_TO_HUMAN,
    Fiche,
    Level,
    Scope,
    ScopeKind,
)
from sillage.memory.search import SearchHit, rewrite_intent

if TYPE_CHECKING:  # the stack is only read here, never built
    from sillage.demo.stack import Stack
    from sillage.kernel.protocols import Principal

#: The common perimeter, searched after the caller's own one.
COMMON = Scope(kind=ScopeKind.COMMON)


@dataclass(frozen=True)
class Consultation:
    """What one consultation brings back, and what it withheld."""

    intent: str
    fiches: tuple[dict[str, Any], ...]
    common: tuple[dict[str, Any], ...]
    hidden: int
    allowed: frozenset[Level]

    @property
    def served_count(self) -> int:
        """How many knowledge objects were served, own perimeter and common."""
        return len(self.fiches) + len(self.common)


def consult(
    stack: Stack,
    *,
    principal: Principal,
    problem: str,
    scope: Scope | None = None,
    limit: int = 0,
) -> Consultation:
    """Ask the memory what one caller may know about a problem.

    ``scope`` is the perimeter the question is asked in; without it the caller's
    home perimeter is used. ``limit`` is how many objects each side may return,
    ``settings.max_fiches`` by default.
    """
    perimeter = scope if scope is not None else _home(principal)
    wanted = limit or stack.settings.max_fiches
    allowed = SERVED_TO_AUTONOMOUS_AGENT if principal.autonomous else SERVED_TO_HUMAN
    own = stack.search(scope=perimeter, query=problem, top_k=wanted)
    shared = stack.search(scope=COMMON, query=problem, top_k=wanted)
    hidden = sum(1 for hit in (*own, *shared) if hit.level not in allowed)
    return Consultation(
        intent=rewrite_intent(problem, stack.model),
        fiches=tuple(view_of(stack, hit) for hit in own if hit.level in allowed),
        common=tuple(view_of(stack, hit) for hit in shared if hit.level in allowed),
        hidden=hidden,
        allowed=allowed,
    )


def _home(principal: Principal) -> Scope:
    """The perimeter of the caller: its client, or the internal one of the MSP."""
    home = principal.home_scope()
    return home if home is not None else Scope(kind=ScopeKind.INTERNAL)


# -- the views the screens show ---------------------------------------------- #
def view_of(stack: Stack, hit: SearchHit) -> dict[str, Any]:
    """One hit as a screen shows it: a fiche, a lesson, or a raw episode."""
    fiche = fiche_of(stack, hit.record_id)
    if fiche is not None:
        return fiche_view(stack, fiche)
    lesson = lesson_of(stack, hit.record_id)
    if lesson is not None:
        return lesson_view(lesson)
    return _empty_view(hit.record_id, hit.content[:120], hit.level, hit.scope.actor_id, hit.freshness_days)


def fiche_view(stack: Stack, fiche: Fiche) -> dict[str, Any]:
    """A fiche with everything that backs it: level, source, rate, freshness."""
    view = served(fiche, stack.now(), stack.settings)
    return {
        "id": fiche.fiche_id,
        "titre": fiche.title,
        "niveau": fiche.level.value,
        "code": view.level_code,
        "portefeuille": fiche.scope.actor_id,
        "taux": round(view.success_rate, 4),
        "fraîcheur": view.freshness_days,
        "origine": fiche.provenance,
        "sources": list(fiche.sources),
        "etapes": list(fiche.steps),
        "a_eviter": list(fiche.avoid),
        "validite": fiche.validity,
    }


def lesson_view(lesson: Any) -> dict[str, Any]:
    """A lesson: what several close episodes teach, not validated yet."""
    return {
        "id": lesson.lesson_id,
        "titre": lesson.use_case,
        "niveau": lesson.level.value,
        "code": LEVEL_CODE[lesson.level],
        "portefeuille": lesson.scope.actor_id,
        "taux": None,
        "fraîcheur": None,
        "origine": f"leçon tirée de {len(lesson.episode_ids)} épisode(s)",
        "sources": list(lesson.episode_ids),
        "etapes": list(lesson.advice),
        "a_eviter": list(lesson.pitfalls),
        "validite": "",
    }


def _empty_view(
    record_id: str, title: str, level: Level, actor_id: str, freshness: int | None
) -> dict[str, Any]:
    """A record the catalogue holds no row for: shown as it is, at its own level."""
    return {
        "id": record_id,
        "titre": title,
        "niveau": level.value,
        "code": LEVEL_CODE[level],
        "portefeuille": actor_id,
        "taux": None,
        "fraîcheur": freshness,
        "origine": "",
        "sources": [],
        "etapes": [],
        "a_eviter": [],
        "validite": "",
    }


def fiche_of(stack: Stack, record_id: str) -> Fiche | None:
    """The fiche a memory record holds the approved text of."""
    for fiche in stack.catalog.list_fiches():
        if fiche.memory_record_id == record_id:
            return fiche
    return None


def lesson_of(stack: Stack, record_id: str) -> Any:
    """The lesson a memory record holds."""
    for lesson in stack.catalog.list_lessons():
        if lesson.memory_record_id == record_id:
            return lesson
    return None


__all__ = [
    "COMMON",
    "Consultation",
    "consult",
    "fiche_of",
    "fiche_view",
    "lesson_of",
    "lesson_view",
    "view_of",
]
