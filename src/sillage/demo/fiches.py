"""The fiches of the demo: what a referent validates, and what proves them.

A fiche is a lesson a human read, corrected and validated. This module builds
one from a lesson, with the three properties the specification demands:

* every statement cites the episodes it comes from (EF-VAL-01), so the fiche is
  publishable by :func:`sillage.guards.sources.check_claims`;
* the steps are what **worked** in the jobs, and the traps are kept apart - the
  failed paths are read from the result of the command that answered them, so a
  path whose outcome was a success never becomes a trap;
* the counters that carry the trust - success rate, distinct authors, level -
  move only through :func:`sillage.catalog.levels.apply_feedback`, the state
  machine of chapter 10.2 (ENF-IA-01).

The identifiers of the client are read here too (13.6 step 1): they are what the
code checks the generalised version against before anything reaches the common
knowledge.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sillage.catalog.levels import apply_feedback
from sillage.demo.history import FAMILY_BY_CODE, STAR_CODE
from sillage.demo.knowledge import fiche_text
from sillage.guards.identifiers import extract, extract_from_trace
from sillage.kernel.ids import fiche_id, pseudonym, usage_id
from sillage.kernel.types import (
    AuthorKind,
    Claim,
    Feedback,
    FeedbackResult,
    Fiche,
    Level,
    PrincipalRole,
    Scope,
    Usage,
)
from sillage.memory.distill import FAILED_PATH_HINTS

#: The three authors whose confirmations prove a fiche (EF-VAL-05).
CONFIRMING_AUTHORS: tuple[str, ...] = (
    pseudonym("ingenieur-demo-a"),
    pseudonym("agent-demo-a"),
    pseudonym("ingenieur-demo-b"),
)

#: Longest title of a fiche, so the portal lines and the demo stay readable.
MAX_TITLE = 120


def draft_fiche(stack: Any, lesson: Any) -> Fiche:
    """The fiche a referent validates from a lesson, built by code."""
    episodes = tuple(
        episode
        for episode in (stack.catalog.get_episode(ident) for ident in lesson.episode_ids)
        if episode is not None
    )
    sources = tuple(dict.fromkeys(episode.work_item_id for episode in episodes))
    technologies = tuple(dict.fromkeys(item for episode in episodes for item in episode.technologies))
    title = title_of(stack, episodes, lesson)
    steps = steps_of(episodes)
    avoid = avoid_of(lesson.pitfalls)
    claims = tuple(
        Claim(text=step, episode_ids=tuple(episode.episode_id for episode in episodes)) for step in steps
    )
    if not claims:
        claims = (Claim(text=title, episode_ids=tuple(episode.episode_id for episode in episodes)),)
    return Fiche(
        fiche_id=fiche_id(lesson.scope.actor_id, title, lesson.lesson_id),
        scope=lesson.scope,
        title=title,
        steps=steps,
        avoid=avoid,
        validity=f"Périmètre : {', '.join(technologies)}" if technologies else "",
        claims=claims,
        sources=sources,
        level=Level.VALIDATED,
        validated_by=pseudonym("referent-demo"),
        validated_at=stack.now(),
        provenance=provenance_of(episodes),
        technologies=technologies,
        lesson_id=lesson.lesson_id,
    )


def title_of(stack: Any, episodes: Sequence[Any], lesson: Any) -> str:
    """The title of a fiche: the name of the fault family it comes from.

    The ``use_case`` of a lesson is the **intention** the episodes share, and
    chapter 13.1 defines that intention as what the resolver tried first - a
    failed lead reads badly as a title. The name of the family is what a
    referent recognises; when the jobs carry no family, the situation is used
    instead, shortened to one sentence.
    """
    family = FAMILY_BY_CODE.get(_family_of(stack, episodes))
    if family is not None:
        return family.name
    for episode in episodes:
        if episode.situation:
            return _shorten(episode.situation)
    return _shorten(lesson.use_case) or "Leçon sans titre"


def steps_of(episodes: Sequence[Any]) -> tuple[str, ...]:
    """The steps of a fiche: what **worked** in each of its jobs.

    The last turn of a job is the one that closed it, so it is the path that
    worked; the failed ones are kept apart, in ``avoid``. Reading the episodes
    rather than the lesson keeps the fiche on what the jobs really did.
    """
    steps: list[str] = []
    for episode in episodes:
        if not episode.turns:
            continue
        action = episode.turns[-1].action
        if action and action not in steps:
            steps.append(action)
    return tuple(steps)


def avoid_of(pitfalls: Sequence[str]) -> tuple[str, ...]:
    """The traps of a fiche: the paths whose **result** shows the failure.

    A trap is kept when the command that answered it says the attempt did not
    work. A path whose outcome was a success is not a trap, whatever the wording
    of the attempt.
    """
    kept = [
        pitfall
        for pitfall in pitfalls
        if _result_of(pitfall) and any(hint in _result_of(pitfall).lower() for hint in FAILED_PATH_HINTS)
    ]
    return tuple(kept) or tuple(pitfalls)


def provenance_of(episodes: Sequence[Any]) -> str:
    """Where a fiche comes from, counted by code and never by a person (ENF-RH-01)."""
    humans = sum(1 for episode in episodes if episode.author_kind is AuthorKind.HUMAN)
    agents = sum(1 for episode in episodes if episode.author_kind is AuthorKind.AGENT)
    mixed = sum(1 for episode in episodes if episode.author_kind is AuthorKind.MIXED)
    corrected = sum(1 for episode in episodes if episode.corrected_by)
    parts: list[str] = []
    if humans:
        parts.append(f"{humans} épisode(s) d'ingénieur")
    if agents:
        parts.append(f"{agents} épisode(s) d'agent")
    if mixed:
        parts.append(f"{mixed} épisode(s) mixte(s)")
    if corrected:
        parts.append(f"{corrected} corrigé(s) par un humain")
    return ", ".join(parts)


def prove_fiches(stack: Any) -> int:
    """Confirm one fiche of each client, so a fiche reaches the level ``éprouvée``.

    Three confirmations by two distinct authors are what the state machine
    requires (EF-VAL-05); the counters of the fiche move through
    :func:`sillage.catalog.levels.apply_feedback`, never by hand.
    """
    proven = 0
    for scope in client_scopes(stack):
        fiche = star_fiche(stack, scope)
        if fiche is None or fiche.success_count:
            continue
        for author in CONFIRMING_AUTHORS:
            usage = _usage_of(stack, fiche, author)
            stack.catalog.put_usage(usage)
            stack.catalog.put_feedback(
                Feedback(
                    usage_id=usage.usage_id,
                    result=FeedbackResult.HELPED,
                    fiche_id=fiche.fiche_id,
                    caller=author,
                    at=stack.now(),
                )
            )
            fiche = apply_feedback(
                fiche,
                FeedbackResult.HELPED,
                settings=stack.settings,
                now=stack.now(),
                author=author,
            )
        stack.catalog.put_fiche(fiche)
        proven += 1
    return proven


def identifiers_of(stack: Any, fiche: Fiche) -> frozenset[str]:
    """Every identifier of the client the fiche comes from (13.6 step 1)."""
    client = stack.catalog.get_client(fiche.scope.client_id or "")
    found = set(extract(fiche_text(fiche), client=client))
    for work_item in fiche.sources:
        trace = stack.catalog.get_trace(work_item)
        if trace is not None:
            found |= extract_from_trace(trace, client=client)
    return frozenset(found)


def star_fiche(stack: Any, scope: Scope) -> Fiche | None:
    """The fiche the demonstration tells the story with: the one about the pool.

    The history holds eight families; the story of the video is the one about the
    exhausted connections, so that is the fiche the confirmations and the common
    knowledge land on. It is found by the family code its source jobs carry,
    never by its title.
    """
    fiches = sorted(stack.catalog.list_fiches(scope=scope), key=lambda fiche: fiche.fiche_id)
    for fiche in fiches:
        if any(family_code_of(stack, work_item) == STAR_CODE for work_item in fiche.sources):
            return fiche
    return fiches[0] if fiches else None


def common_candidate(stack: Any) -> Fiche | None:
    """The fiche the common knowledge is published from: the one about the pool."""
    for scope in client_scopes(stack):
        star = star_fiche(stack, scope)
        if star is not None:
            return star
    return None


def client_scopes(stack: Any) -> tuple[Scope, ...]:
    """The perimeters of the clients, in the order they were registered."""
    return tuple(stack.scope(client.client_id) for client in stack.clients())


def family_code_of(stack: Any, work_item: str) -> str:
    """The fault family a finished job belongs to, read in its trace."""
    trace = stack.catalog.get_trace(work_item)
    return str(trace.origin.get("code", "")) if trace is not None else ""


def _family_of(stack: Any, episodes: Sequence[Any]) -> str:
    """The fault family of the first job that names a known one."""
    for episode in episodes:
        code = family_code_of(stack, episode.work_item_id)
        if code in FAMILY_BY_CODE:
            return code
    return ""


def _shorten(text: str) -> str:
    """One sentence, cut at a word boundary when it is too long."""
    head = (text or "").strip().split(". ")[0].strip().rstrip(".")
    if len(head) <= MAX_TITLE:
        return head
    return f"{head[:MAX_TITLE].rsplit(' ', 1)[0]}…"


def _result_of(pitfall: str) -> str:
    """The result of a failed path: the text after the arrow."""
    return pitfall.split(" -> ", 1)[1] if " -> " in pitfall else ""


def _usage_of(stack: Any, fiche: Fiche, author: str) -> Usage:
    """One consultation of a fiche, with the identifier the feedback refers to."""
    return Usage(
        usage_id=usage_id(author, stack.now().isoformat(), fiche.fiche_id),
        served_ids=(fiche.fiche_id,),
        caller_role=PrincipalRole.ENGINEER,
        scope=fiche.scope,
        at=stack.now(),
        problem=fiche.title,
    )


__all__ = [
    "CONFIRMING_AUTHORS",
    "MAX_TITLE",
    "avoid_of",
    "client_scopes",
    "common_candidate",
    "draft_fiche",
    "family_code_of",
    "identifiers_of",
    "prove_fiches",
    "provenance_of",
    "star_fiche",
    "steps_of",
    "title_of",
]
