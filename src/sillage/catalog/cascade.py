"""Cascade deletion of one perimeter: the rules, shared by both stores.

Forgetting a client, or the work items of a client (EF-ENT-03, specification
17.2), must be complete on one side and honest on the other: everything the
perimeter owns disappears, and every fiche **elsewhere** whose sources pointed at
a deleted work item is reported instead of being silently kept or silently
removed. A common lesson built from a client episode is exactly that case: the
text may stay, the trail back to the client must not.

The rules live here, once: :func:`plan` reads the rows of the catalogue and
returns a :class:`CascadePlan` naming what leaves and what is merely flagged.
``local_cascade`` applies it to dictionaries, ``dynamo_rows`` to table items, so
the two stores cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from sillage.catalog.local_state import latest_per_fiche
from sillage.kernel.types import Episode, Feedback, Fiche, Lesson, Scope, Trace, Usage

#: Keys of the report of a cascade, one per family of chapter 14, plus the flags.
CASCADE_KEYS = (
    "travaux",
    "episodes",
    "lecons",
    "fiches",
    "usages",
    "retours",
    "fiches_signalees",
)


@dataclass(frozen=True)
class CascadePlan:
    """What a cascade deletes, and what it only reports."""

    works: frozenset[str] = frozenset()
    episodes: frozenset[str] = frozenset()
    lessons: frozenset[str] = frozenset()
    fiches: frozenset[str] = frozenset()
    usages: frozenset[str] = frozenset()
    retours: tuple[Feedback, ...] = ()
    flagged: tuple[Fiche, ...] = field(default=())

    def report(self) -> dict[str, int]:
        """Counts of the deletion, one key per family, zeros included."""
        counts = {
            "travaux": len(self.works),
            "episodes": len(self.episodes),
            "lecons": len(self.lessons),
            "fiches": len(self.fiches),
            "usages": len(self.usages),
            "retours": len(self.retours),
            "fiches_signalees": len(self.flagged),
        }
        return {key: counts[key] for key in CASCADE_KEYS}


def plan(
    *,
    scope: Scope,
    traces: Sequence[Trace],
    episodes: Sequence[Episode],
    lessons: Sequence[Lesson],
    fiches: Sequence[Fiche],
    usages: Sequence[Usage],
    feedbacks: Sequence[Feedback],
) -> CascadePlan:
    """Select every row a cascade removes, and every fiche it leaves flagged.

    The perimeter goes first: its works, then the episodes and lessons built on
    them (a lesson built on a doomed episode leaves wherever it lives, because it
    would otherwise keep a trail back to the client), then its fiches with every
    version, then the consultations and their answers.
    """
    works = frozenset(row.work_item_id for row in traces if row.scope == scope)
    episodes_ = frozenset(
        row.episode_id for row in episodes if row.scope == scope or row.work_item_id in works
    )
    lessons_ = frozenset(
        row.lesson_id for row in lessons if row.scope == scope or episodes_.intersection(row.episode_ids)
    )
    fiches_ = frozenset(row.fiche_id for row in fiches if row.scope == scope)
    usages_ = frozenset(
        row.usage_id for row in usages if row.scope == scope or fiches_.intersection(row.served_ids)
    )
    answers = tuple(row for row in feedbacks if row.usage_id in usages_ or row.fiche_id in fiches_)
    return CascadePlan(
        works=works,
        episodes=episodes_,
        lessons=lessons_,
        fiches=fiches_,
        usages=usages_,
        retours=answers,
        flagged=dependent_fiches(fiches, fiches_, works),
    )


def dependent_fiches(
    fiches: Sequence[Fiche], doomed: frozenset[str], works: frozenset[str]
) -> tuple[Fiche, ...]:
    """Latest versions outside the perimeter whose sources lose their work item.

    They are **kept** and reported, never deleted: chapter 9.8 gives the decision
    to a referent (EF-ENT-02). A fiche citing a dead source is a fiche whose
    provenance must be repaired, not silently emptied.
    """
    kept = [fiche for fiche in fiches if fiche.fiche_id not in doomed]
    flagged = [row for row in latest_per_fiche(kept) if works.intersection(row.sources)]
    return tuple(sorted(flagged, key=lambda row: (row.scope.actor_id, row.fiche_id)))
