"""The approved text, written verbatim in Memory (specification 12.4).

The catalogue holds the exact state of a knowledge object - its level, its
counters, its sources - and Memory holds the text a search finds. A fiche that
lives only in the catalogue is invisible to :func:`sillage.memory.search.search`,
because that function reads records and joins them back to the catalogue through
``memory_record_id``. This module is the one place that writes that pair, so the
demo, the tools and the portal all publish knowledge the same way.

Two rules come straight from the specification:

* the text of an approved fiche is written **verbatim** (EF-GEN-07): not one
  character is rewritten on the way in, and a record of the common space carries
  no identifier that would let another perimeter walk back to the episode it came
  from;
* no figure is written here (ENF-IA-01): the level code is the one the kernel
  computed, and the success rate and the freshness stay in the catalogue, where
  :mod:`sillage.catalog.scoring` reads them.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING

from sillage.kernel.ids import slug
from sillage.kernel.protocols import MemoryRecord
from sillage.kernel.types import LEVEL_CODE, Fiche, Lesson, Level
from sillage.memory.local_records import KIND_APPROVED
from sillage.memory.namespaces import fiche_namespace, reflection_namespace

if TYPE_CHECKING:  # the stack is only read here, never built
    from sillage.demo.stack import Stack

#: The ``kind`` metadata of a lesson the reflection of one perimeter produced.
KIND_LESSON = "lesson"

#: The ``kind`` metadata of a lesson a referent published in the common space.
KIND_COMMON = "common"


def fiche_text(fiche: Fiche) -> str:
    """The French text of a fiche, as Memory stores it and a search reads it."""
    parts = [f"Fiche : {fiche.title}"]
    if fiche.body:
        parts.append(fiche.body)
    parts.extend(_bullets("Étapes", fiche.steps))
    parts.extend(_bullets("À éviter", fiche.avoid))
    if fiche.validity:
        parts.append(f"Validité : {fiche.validity}")
    if fiche.provenance:
        parts.append(f"Origine : {fiche.provenance}")
    return "\n".join(part for part in parts if part)


def lesson_text(lesson: Lesson) -> str:
    """The French text of a lesson: its use case, its advice and its traps."""
    parts = [f"Leçon : {lesson.use_case}"]
    parts.extend(_bullets("Conseils", lesson.advice))
    parts.extend(_bullets("Pièges à éviter", lesson.pitfalls))
    return "\n".join(part for part in parts if part)


def _bullets(label: str, lines: tuple[str, ...]) -> list[str]:
    """One labelled block of bullets, empty when there is nothing to list."""
    if not lines:
        return []
    return [f"{label} :", *(f"- {line}" for line in lines if line)]


def fiche_record(fiche: Fiche, *, namespace: str, created_at: datetime | None = None) -> MemoryRecord:
    """The Memory record of a fiche: its text, and the metadata a search filters on."""
    return MemoryRecord(
        record_id=fiche.fiche_id,
        namespace=namespace,
        content=fiche_text(fiche),
        metadata={
            "kind": KIND_APPROVED,
            "scope": fiche.scope.actor_id,
            "ficheId": fiche.fiche_id,
            "niveau": LEVEL_CODE[fiche.level],
            "titre": fiche.title,
            "intent": fiche.title,
            "techno": ",".join(fiche.technologies[:4]),
        },
        created_at=created_at,
    )


def lesson_record(lesson: Lesson, *, namespace: str, created_at: datetime | None = None) -> MemoryRecord:
    """The Memory record of a lesson, of one perimeter or of the common space."""
    return MemoryRecord(
        record_id=lesson.lesson_id,
        namespace=namespace,
        content=lesson_text(lesson),
        metadata={
            "kind": KIND_COMMON if lesson.scope.is_common else KIND_LESSON,
            "scope": lesson.scope.actor_id,
            "lessonId": lesson.lesson_id,
            "niveau": LEVEL_CODE[lesson.level],
            "intent": lesson.use_case,
        },
        created_at=created_at,
    )


def publish_fiche(stack: Stack, fiche: Fiche) -> Fiche:
    """Write one version of a fiche in Memory and in the catalogue; return it stored.

    The record id is the fiche id, stable across versions, so a correction
    replaces the text a search finds instead of leaving two copies of one fiche in
    the index (EF-ENT-04).
    """
    namespace = fiche_namespace(stack.settings, fiche.scope)
    record = fiche_record(fiche, namespace=namespace, created_at=stack.now())
    stack.memory.put_records([record])
    stored = replace(fiche, memory_record_id=record.record_id)
    stack.catalog.put_fiche(stored)
    return stored


def publish_lesson(stack: Stack, lesson: Lesson) -> Lesson:
    """Write a lesson in Memory and in the catalogue; return it stored."""
    namespace = _lesson_namespace(stack, lesson)
    record = lesson_record(lesson, namespace=namespace, created_at=stack.now())
    stack.memory.put_records([record])
    stored = replace(lesson, memory_record_id=record.record_id)
    stack.catalog.put_lesson(stored)
    return stored


def skill_folder(fiche: Fiche) -> dict[str, str]:
    """The folder of a skill exported from a fiche, in the shape of annex F.

    **Provisional** - the export belongs to the ``tools`` lot (CONTRACT §7,
    ``tools.skill.skill_folder``). The shape is the one of annex F, so the
    demonstration shows the registry with the very file Kiro would load.
    """
    name = slug(fiche.title)
    lines = [
        "---",
        f"name: {name}",
        f"description: {skill_description(fiche)}",
        "---",
        "",
        f"# {fiche.title}",
        "",
        f"Source : fiche {fiche.fiche_id} de Sillage, {_level_word(fiche)}, "
        f"dernière confirmation le {_date_of(fiche)}.",
        "",
        "## Quand l'utiliser",
        (f"- {fiche.validity}" if fiche.validity else "- Le problème décrit par la fiche."),
        "",
        "## Étapes",
        *(f"{number}. {step}" for number, step in enumerate(fiche.steps, start=1)),
        "",
        "## À ne pas faire",
        *(f"- {trap}" for trap in fiche.avoid),
        "",
        "## Après usage",
        "Appeler l'outil `report_outcome` de Sillage pour dire si ces étapes ont réglé le problème.",
    ]
    return {"SKILL.md": "\n".join(line for line in lines if line is not None)}


def skill_description(fiche: Fiche) -> str:
    """The one line that tells an MCP client when to load the skill."""
    subject = fiche.title[0].lower() + fiche.title[1:] if fiche.title else "un incident"
    return f"À utiliser quand {subject}. Indique quoi vérifier d'abord et ce qu'il ne faut pas faire."


def _level_word(fiche: Fiche) -> str:
    """The level of a fiche, in the words of the specification."""
    return {
        Level.RAW: "brute",
        Level.PROPOSED: "proposée",
        Level.VALIDATED: "validée",
        Level.PROVEN: "éprouvée",
        Level.SET_ASIDE: "mise à l'écart",
        Level.WITHDRAWN: "retirée",
    }.get(fiche.level, fiche.level.value)


def _date_of(fiche: Fiche) -> str:
    """The date of the last confirmation, in French, or a dash when there is none."""
    if fiche.last_confirmation is None:
        return "—"
    return fiche.last_confirmation.strftime("%d %B %Y")


def _lesson_namespace(stack: Stack, lesson: Lesson) -> str:
    """Where a lesson lives: the common space, or the space of its perimeter."""
    if lesson.scope.is_common:
        return stack.settings.common_namespace()
    return reflection_namespace(stack.settings, lesson.scope)


__all__ = [
    "KIND_COMMON",
    "KIND_LESSON",
    "fiche_record",
    "fiche_text",
    "lesson_record",
    "lesson_text",
    "publish_fiche",
    "publish_lesson",
    "skill_description",
    "skill_folder",
]
