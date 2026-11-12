"""The approved text, written verbatim in Memory (specification 12.4).

The catalogue holds the exact state of a knowledge object - its level, its
counters, its sources - and Memory holds the text a search finds. A fiche that
lives only in the catalogue is invisible to :func:`sillage.memory.search.search`,
because that function reads records and joins them back to the catalogue
through ``memory_record_id``. This module is the one place that writes that
pair, so the tools, the demo and the portal all publish knowledge the same way.

Two rules come straight from the specification:

* the text of an approved fiche is written **verbatim** (EF-GEN-07): not one
  character is rewritten on the way in, and a record of the common space carries
  no identifier that would let another perimeter walk back to the episode it
  came from;
* no figure is written here (ENF-IA-01): the level code is the one the kernel
  computed, and the success rate and the freshness stay in the catalogue, where
  :mod:`sillage.catalog.scoring` reads them.
"""

from __future__ import annotations

from datetime import datetime

from sillage.kernel.ids import slug
from sillage.kernel.protocols import MemoryRecord
from sillage.kernel.types import LEVEL_CODE, Fiche, Lesson
from sillage.memory.local_records import KIND_APPROVED

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


def _bullets(label: str, lines: tuple[str, ...]) -> list[str]:
    """One labelled block of bullets, empty when there is nothing to list."""
    if not lines:
        return []
    return [f"{label} :", *(f"- {line}" for line in lines if line)]


def name_of(fiche: Fiche) -> str:
    """The name of a skill folder: the slug of the title of the fiche."""
    return slug(fiche.title)


__all__ = [
    "KIND_COMMON",
    "KIND_LESSON",
    "fiche_record",
    "fiche_text",
    "lesson_record",
    "lesson_text",
    "name_of",
]
