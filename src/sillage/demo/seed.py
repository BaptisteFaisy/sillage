"""Seeding the demo: the history, the lessons, the fiches, the common knowledge.

``seed_stack`` turns an empty stack into a demo that has something to show: a
history of finished jobs, the lessons the reflection draws from it, the fiches a
referent validated, one common lesson, and the usages that prove a fiche.

The starting history comes from ``data/generated/history.json`` when the ``data``
lot has produced it, and from the deterministic fallback of
:mod:`sillage.demo.history` when it has not. **Nothing here may fail because
``data/`` is empty** (CONTRACT.md rule 7): an unreadable file, a malformed entry
or a missing directory all fall back on the built-in history, and the counters say
which source was used.

Every figure the demo shows is computed by code (ENF-IA-01): the levels come from
the state machine of :mod:`sillage.catalog.levels`, the rates from
:mod:`sillage.catalog.scoring`, and no author is ever grouped (ENF-RH-01).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sillage.capture import sample
from sillage.demo.fiches import common_candidate, draft_fiche, identifiers_of, prove_fiches
from sillage.demo.history_file import families_of, history_payloads, traces_of
from sillage.demo.knowledge import publish_fiche, publish_lesson
from sillage.guards.generalize import approve_common
from sillage.kernel.ids import pseudonym
from sillage.kernel.types import Level, Scope, ScopeKind

#: The referent who validates the fiches of the demo, under a pseudonym.
REFERENT = pseudonym("referent-demo")

#: The generalised text published in the common knowledge (chapter 13.6).
COMMON_TEXT = (
    "Après un changement de configuration, un service peut épuiser les connexions de sa base : "
    "comparer la taille du groupe de connexions avant et après le changement, la multiplier par le "
    "nombre d'instances, puis comparer le résultat au maximum de la base. Revenir à la valeur "
    "précédente quand le produit dépasse ce maximum. Ne pas redémarrer le service : les connexions "
    "reviennent au maximum en quelques minutes. Ne pas augmenter la taille de la base avant d'avoir "
    "vérifié le groupe de connexions."
)


# -- the public surface ------------------------------------------------------ #
def seed_stack(stack: Any, *, path: str | Path | None = None) -> dict[str, Any]:
    """Fill a stack with the demo, and return the counters of what was loaded.

    The counters describe the **state** of the stack, not the work of this call,
    so two runs of the seed give the same numbers: the demo is reproducible.
    ``path`` is the history file to read; without it the generated one is tried,
    then the built-in fallback.
    """
    loaded, source, families = _load(stack, path)
    reflect_perimeters(stack)
    promote_fiches(stack)
    prove_fiches(stack)
    publish_common_knowledge(stack)
    return counters(stack, loaded=loaded, source=source, families=families)


def load_history(stack: Any, path: str | Path | None = None) -> int:
    """Load the history into the stack; return how many jobs were ingested.

    The generated file is used when it exists, the deterministic fallback
    otherwise. Loading twice is harmless: the capture recognises the traces it
    already received and changes nothing (EF-CAP-11, R3).
    """
    return _load(stack, path)[0]


# -- the history -------------------------------------------------------------- #
def _load(stack: Any, path: str | Path | None) -> tuple[int, str, int]:
    """Ingest the reference jobs and the history; return what was loaded."""
    payloads, source = history_payloads(stack, path)
    loaded = 0
    duplicates = 0
    for trace in [*sample.all_samples(), *traces_of(stack, payloads)]:
        report = stack.ingest.ingest_trace(trace)
        if report.duplicate:
            duplicates += 1
        else:
            loaded += 1
    return loaded, source, families_of(payloads)


# -- the reflection, the fiches, the common knowledge ------------------------- #
def reflect_perimeters(stack: Any) -> int:
    """Draw the lessons of every perimeter, one perimeter at a time (EF-DIS-02)."""
    published = 0
    for scope in stack.perimeters():
        for lesson in stack.memory.reflections(actor_id=scope.actor_id):
            publish_lesson(stack, lesson)
            published += 1
    return published


def promote_fiches(stack: Any) -> int:
    """Turn the lessons of the clients into validated fiches; return how many."""
    created = 0
    for lesson in sorted(stack.catalog.list_lessons(), key=lambda item: item.lesson_id):
        if lesson.scope.is_common or lesson.level is not Level.PROPOSED:
            continue
        draft = draft_fiche(stack, lesson)
        if stack.catalog.get_fiche(draft.fiche_id) is not None:
            continue
        publish_fiche(stack, draft)
        created += 1
    return created


def publish_common_knowledge(stack: Any) -> int:
    """Publish the common lesson of chapter 13.6; return how many were published.

    The five vetoes of the chapter run here, by code: the contract of the client,
    the sources of the fiche, the identifiers of the client, the guardrails and
    the approval of a human referent. Only the rewrite is text, and it is the
    generalised text of :data:`COMMON_TEXT`.
    """
    common = Scope(kind=ScopeKind.COMMON)
    if stack.catalog.list_lessons(scope=common):
        return 0
    source = common_candidate(stack)
    if source is None:
        return 0
    client = stack.catalog.get_client(source.scope.client_id or "")
    lesson = approve_common(
        fiche=source,
        rewritten=COMMON_TEXT,
        identifiers=identifiers_of(stack, source),
        guardrails=stack.guardrails,
        approver=REFERENT,
        client=client,
    )
    publish_lesson(stack, lesson)
    return 1


# -- the counters ------------------------------------------------------------- #
def counters(stack: Any, *, loaded: int, source: str, families: int) -> dict[str, Any]:
    """The state of the stack, as the counters of the seeding."""
    fiches = tuple(stack.catalog.list_fiches())
    lessons = tuple(stack.catalog.list_lessons())
    episodes = tuple(stack.catalog.list_episodes(limit=0))
    return {
        "clients": len(stack.clients()),
        "perimeters": len(stack.perimeters()),
        "traces": _traces_count(stack),
        "episodes": len(episodes),
        "lessons": len(lessons),
        "common_lessons": sum(1 for lesson in lessons if lesson.scope.is_common),
        "fiches": len(fiches),
        "proven": sum(1 for fiche in fiches if fiche.level is Level.PROVEN),
        "validated": sum(1 for fiche in fiches if fiche.level is Level.VALIDATED),
        "records": _records_count(stack),
        "families": families,
        "loaded": loaded,
        "history_source": source,
    }


def _traces_count(stack: Any) -> int:
    """How many finished jobs the catalogue holds."""
    traces = getattr(stack.catalog, "traces", None)
    return len(traces) if isinstance(traces, dict) else 0


def _records_count(stack: Any) -> int:
    """How many records the memory holds, when the backend can say."""
    reader = getattr(stack.memory, "records", None)
    return len(reader()) if callable(reader) else 0


__all__ = [
    "COMMON_TEXT",
    "REFERENT",
    "counters",
    "load_history",
    "promote_fiches",
    "publish_common_knowledge",
    "reflect_perimeters",
    "seed_stack",
]
