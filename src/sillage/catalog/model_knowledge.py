"""Rows of the knowledge objects: episode, lesson and fiche (chapter 14).

The three families share one shape: a perimeter, an identifier, a text, and the
exact state that decides how far the object may travel (its level, its counters,
its sources). They have their own module so the serialisation of the ``Travail``
row stays readable, and because these three are the rows a fiche's provenance
walks: a fiche cites episodes, a lesson cites episodes, an episode cites a work
item.

Every optional attribute is omitted rather than written as ``NULL``, and every
reader accepts a missing or unreadable field, so an older row keeps being read.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sillage.catalog.attrs import (
    ATTR,
    ENTITY,
    instant,
    integer,
    list_of,
    member,
    moment,
    n,
    put_attr,
    real,
    s,
    scope_from_item,
    scope_to_item,
    string_set,
    strings,
    text,
)
from sillage.kernel.types import AuthorKind, Claim, Episode, Fiche, Lesson, Level, Outcome, Source, Turn

#: Entity names of the three families, written in the ``entityType`` attribute.
EPISODE_ENTITY = "episode"
LESSON_ENTITY = "lesson"
FICHE_ENTITY = "fiche"


def _turns_to_attr(turns: Iterable[Turn]) -> ATTR:
    """Turns as an ordered list of maps: what was intended, done and obtained."""
    return list_of(
        {
            "M": {
                "action": s(turn.action),
                "reasoning": s(turn.reasoning),
                "result": s(turn.result),
            }
        }
        for turn in turns
    )


def _turns_from_attr(item: Mapping[str, Any], key: str = "turns") -> tuple[Turn, ...]:
    """Read the turns of an episode row, empty when missing."""
    value = item.get(key)
    if not isinstance(value, dict) or not isinstance(value.get("L"), list):
        return ()
    return tuple(
        Turn(
            action=text(entry.get("M", {}), "action"),
            reasoning=text(entry.get("M", {}), "reasoning"),
            result=text(entry.get("M", {}), "result"),
        )
        for entry in value["L"]
        if isinstance(entry, dict)
    )


def episode_to_item(episode: Episode) -> dict:
    """Row of one finished job as Memory summarised it (chapter 14 ``Episode``)."""
    item = {
        ENTITY: s(EPISODE_ENTITY),
        "episodeId": s(episode.episode_id),
        "scope": scope_to_item(episode.scope),
        "workItemId": s(episode.work_item_id),
        "situation": s(episode.situation),
        "intent": s(episode.intent),
        "turns": _turns_to_attr(episode.turns),
        "outcome": s(episode.outcome.value),
        "justification": s(episode.justification),
        "source": s(episode.source.value),
        "authorKind": s(episode.author_kind.value),
        "author": s(episode.author),
        "level": s(episode.level.value),
    }
    for key, values in (
        ("teachings", episode.teachings),
        ("failedPaths", episode.failed_paths),
        ("technologies", episode.technologies),
    ):
        put_attr(item, key, string_set(values))
    for key, value in (
        ("memoryRecordId", episode.memory_record_id),
        ("rejectedDiagnosis", episode.rejected_diagnosis),
        ("correctedBy", episode.corrected_by),
    ):
        put_attr(item, key, s(value) if value else None)
    put_attr(item, "createdAt", instant(episode.created_at))
    return item


def episode_from_item(item: Mapping[str, Any]) -> Episode:
    """Read an episode row, tolerant to missing fields."""
    return Episode(
        episode_id=text(item, "episodeId"),
        scope=scope_from_item(item),
        work_item_id=text(item, "workItemId"),
        situation=text(item, "situation"),
        intent=text(item, "intent"),
        turns=_turns_from_attr(item),
        outcome=member(Outcome, item, "outcome", Outcome.UNKNOWN),
        justification=text(item, "justification"),
        teachings=strings(item, "teachings"),
        failed_paths=strings(item, "failedPaths"),
        memory_record_id=text(item, "memoryRecordId") or None,
        source=member(Source, item, "source", Source.GUICHET),
        author_kind=member(AuthorKind, item, "authorKind", AuthorKind.HUMAN),
        author=text(item, "author"),
        technologies=strings(item, "technologies"),
        level=member(Level, item, "level", Level.RAW),
        created_at=moment(item, "createdAt"),
        rejected_diagnosis=text(item, "rejectedDiagnosis"),
        corrected_by=text(item, "correctedBy"),
    )


def lesson_to_item(lesson: Lesson) -> dict:
    """Row of a reflection: what several close episodes teach (chapter 14)."""
    item = {
        ENTITY: s(LESSON_ENTITY),
        "lessonId": s(lesson.lesson_id),
        "scope": scope_to_item(lesson.scope),
        "useCase": s(lesson.use_case),
        "confidence": n(lesson.confidence),
        "level": s(lesson.level.value),
    }
    for key, values in (
        ("advice", lesson.advice),
        ("pitfalls", lesson.pitfalls),
        ("episodeIds", lesson.episode_ids),
    ):
        put_attr(item, key, string_set(values))
    put_attr(item, "memoryRecordId", s(lesson.memory_record_id) if lesson.memory_record_id else None)
    put_attr(item, "createdAt", instant(lesson.created_at))
    return item


def lesson_from_item(item: Mapping[str, Any]) -> Lesson:
    """Read a lesson row, tolerant to missing fields."""
    return Lesson(
        lesson_id=text(item, "lessonId"),
        scope=scope_from_item(item),
        use_case=text(item, "useCase"),
        advice=strings(item, "advice"),
        pitfalls=strings(item, "pitfalls"),
        confidence=real(item, "confidence", 0.5),
        episode_ids=strings(item, "episodeIds"),
        memory_record_id=text(item, "memoryRecordId") or None,
        level=member(Level, item, "level", Level.PROPOSED),
        created_at=moment(item, "createdAt"),
    )


def _claims_to_attr(claims: Iterable[Claim]) -> ATTR:
    """Claims as a list of maps: every statement keeps its episode references."""
    return list_of(
        {
            "M": {
                "text": s(claim.text),
                "episodeIds": list_of([s(found) for found in claim.episode_ids]),
            }
        }
        for claim in claims
    )


def _claims_from_attr(item: Mapping[str, Any], key: str = "claims") -> tuple[Claim, ...]:
    """Read the claims of a fiche row, empty when missing."""
    value = item.get(key)
    if not isinstance(value, dict) or not isinstance(value.get("L"), list):
        return ()
    claims = []
    for entry in value["L"]:
        body = entry.get("M", {}) if isinstance(entry, dict) else {}
        claims.append(Claim(text=text(body, "text"), episode_ids=strings(body, "episodeIds")))
    return tuple(claims)


def fiche_to_item(fiche: Fiche) -> dict:
    """Row of one version of a fiche, with the exact counters of its trust.

    The family is keyed by ``ficheId`` **and** ``version`` (chapter 14): a
    correction writes a new row and the previous one stays readable, which is
    what ``versions.history`` walks (EF-ENT-04).
    """
    item = {
        ENTITY: s(FICHE_ENTITY),
        "ficheId": s(fiche.fiche_id),
        "scope": scope_to_item(fiche.scope),
        "version": n(fiche.version),
        "title": s(fiche.title),
        "body": s(fiche.body),
        "steps": list_of([s(value) for value in fiche.steps]),
        "avoid": list_of([s(value) for value in fiche.avoid]),
        "validity": s(fiche.validity),
        "claims": _claims_to_attr(fiche.claims),
        "sources": list_of([s(value) for value in fiche.sources]),
        "level": s(fiche.level.value),
        "successCount": n(fiche.success_count),
        "failureCount": n(fiche.failure_count),
        "noFeedbackCount": n(fiche.no_feedback_count),
        "consecutiveFailures": n(fiche.consecutive_failures),
    }
    for key, value in (
        ("validatedBy", fiche.validated_by),
        ("memoryRecordId", fiche.memory_record_id),
        ("provenance", fiche.provenance),
        ("setAsideReason", fiche.set_aside_reason),
        ("lessonId", fiche.lesson_id),
    ):
        put_attr(item, key, s(value) if value else None)
    put_attr(item, "distinctAuthors", string_set(fiche.distinct_authors))
    put_attr(item, "technologies", string_set(fiche.technologies))
    put_attr(item, "validatedAt", instant(fiche.validated_at))
    put_attr(item, "lastConfirmation", instant(fiche.last_confirmation))
    return item


def fiche_from_item(item: Mapping[str, Any]) -> Fiche:
    """Read a fiche row, tolerant to missing fields."""
    return Fiche(
        fiche_id=text(item, "ficheId"),
        scope=scope_from_item(item),
        version=integer(item, "version", 1),
        title=text(item, "title"),
        body=text(item, "body"),
        steps=strings(item, "steps"),
        avoid=strings(item, "avoid"),
        validity=text(item, "validity"),
        claims=_claims_from_attr(item),
        sources=strings(item, "sources"),
        level=member(Level, item, "level", Level.PROPOSED),
        validated_by=text(item, "validatedBy"),
        validated_at=moment(item, "validatedAt"),
        memory_record_id=text(item, "memoryRecordId") or None,
        success_count=integer(item, "successCount"),
        failure_count=integer(item, "failureCount"),
        no_feedback_count=integer(item, "noFeedbackCount"),
        consecutive_failures=integer(item, "consecutiveFailures"),
        distinct_authors=strings(item, "distinctAuthors"),
        last_confirmation=moment(item, "lastConfirmation"),
        provenance=text(item, "provenance"),
        technologies=strings(item, "technologies"),
        set_aside_reason=text(item, "setAsideReason"),
        lesson_id=text(item, "lessonId"),
    )


__all__ = [
    "EPISODE_ENTITY",
    "FICHE_ENTITY",
    "LESSON_ENTITY",
    "episode_from_item",
    "episode_to_item",
    "fiche_from_item",
    "fiche_to_item",
    "lesson_from_item",
    "lesson_to_item",
]
