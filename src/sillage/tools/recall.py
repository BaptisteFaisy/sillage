"""The consultation: what a caller may know about a problem (chapter 13.4).

The answer of ``recall`` is built in the order of the specification:

1. the problem is reformulated as an **intention** (EF-RES-09), because the
   episodes are indexed on their intention and a raw error message finds
   nothing;
2. the spaces interrogated are the ones the **token** carries (10.4) - the
   perimeter of the caller, then the common knowledge;
3. the catalogue gives each hit its level, its success rate and its freshness -
   three figures computed by code, never by a model (ENF-IA-01);
4. the hits are ranked: the perimeter before the common knowledge, a proven
   fiche before a validated one (EF-RES-04);
5. what the caller may not read is dropped, and **said**: an autonomous agent is
   told how many non validated elements exist instead of receiving them
   (EF-RES-05);
6. the answer is trimmed until it fits in the token budget (ENF-PERF-03), and it
   carries the usage identifier the feedback will attach to (EF-RET-01).

``recall_all`` is the same consultation for a human or an assisted agent: it
adds the lessons and the non validated episodes, **signalled as such** by their
level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sillage.catalog.scoring import estimate_tokens, served
from sillage.kernel.errors import (
    BackendUnavailable,
    NotFound,
    PolicyDenied,
    SillageError,
    ValidationError,
)
from sillage.kernel.protocols import Principal
from sillage.kernel.types import (
    SERVED_TO_AUTONOMOUS_AGENT,
    SERVED_TO_HUMAN,
    AuthorKind,
    Episode,
    Lesson,
    Level,
    Outcome,
    RecallResult,
    Scope,
    ServedFiche,
    Usage,
)
from sillage.memory.search import SearchHit, hit_of, rank, rewrite_intent
from sillage.tools.errors import quietly, tool_error
from sillage.tools.gateway import (
    caller_of,
    enforce,
    episode_of,
    fiche_of,
    lesson_of,
    namespaces_for,
    now_of,
    perimeter_of,
    store_fiche,
    usage_identifier,
)
from sillage.tools.views import episode_view, fiche_view, lesson_view

#: The levels a fiche may carry in the compact answer of ``recall``.
RECALL_LEVELS: frozenset[Level] = frozenset({Level.VALIDATED, Level.PROVEN, Level.SET_ASIDE})

#: The levels a fiche may carry in the answer of ``recall_all``: the draft too.
COMPLETE_LEVELS: frozenset[Level] = RECALL_LEVELS | {Level.PROPOSED}


@dataclass
class _Bundle:
    """What one consultation holds, before the token budget trims it."""

    fiches: list[ServedFiche] = field(default_factory=list)
    common: list[ServedFiche] = field(default_factory=list)
    episodes: list[Episode] = field(default_factory=list)
    lessons: list[Lesson] = field(default_factory=list)
    traps: list[str] = field(default_factory=list)


def recall(
    stack: Any,
    *,
    client_id: str,
    problem: str,
    techno: str = "",
    principal: Principal | None = None,
) -> RecallResult | dict[str, Any]:
    """The validated knowledge of a perimeter, for a described problem.

    Everybody may call it: the agent of the client, the engineer, the referent
    (table 10.5). A refusal is returned, never raised.
    """
    caller = caller_of(principal, client_id=client_id)
    try:
        enforce(stack, "recall", caller, clientId=client_id, problem=problem, techno=techno)
    except PolicyDenied as exc:
        return tool_error(exc)
    try:
        return _consult(stack, caller, client_id=client_id, problem=problem, complete=False)
    except BackendUnavailable as exc:
        return _degraded(stack, caller, client_id, problem, exc.message)


def recall_all(
    stack: Any,
    *,
    client_id: str,
    problem: str,
    techno: str = "",
    principal: Principal | None = None,
) -> RecallResult | dict[str, Any]:
    """The same consultation, plus the lessons and the non validated episodes.

    Humans and assisted agents only: the policy refuses an autonomous agent
    before the tool runs (rule D.2, R8).
    """
    caller = caller_of(principal, client_id=client_id)
    try:
        enforce(stack, "recall_all", caller, clientId=client_id, problem=problem, techno=techno)
    except PolicyDenied as exc:
        return tool_error(exc)
    try:
        return _consult(stack, caller, client_id=client_id, problem=problem, complete=True)
    except BackendUnavailable as exc:
        return _degraded(stack, caller, client_id, problem, exc.message)


def get_item(stack: Any, *, id: str, principal: Principal | None = None) -> dict[str, Any]:
    """One knowledge object, whole, when the caller may read it.

    The perimeter of the object is checked by the tool: a fiche or an episode of
    another client is not returned to anybody (specification 10.4).
    """
    caller = caller_of(principal)
    try:
        enforce(stack, "get_item", caller, id=id)
    except PolicyDenied as exc:
        return tool_error(exc)
    try:
        return _item(stack, caller, id)
    except BackendUnavailable as exc:
        return {"message": exc.message, "degraded": True, "refused": False}
    except SillageError as exc:
        return tool_error(exc)


# -- the consultation -------------------------------------------------------- #
def _consult(
    stack: Any,
    caller: Principal,
    *,
    client_id: str,
    problem: str,
    complete: bool,
) -> RecallResult:
    """Build the answer of one consultation, from the hits to the usage row."""
    settings = stack.settings
    now = now_of(stack)
    perimeter = perimeter_of(caller, client_id)
    namespaces = namespaces_for(settings, caller, client_id)
    intent = rewrite_intent(problem, stack.model)
    hits = _hits(stack, namespaces, intent, now, perimeter)
    bundle = _bundle(stack, hits, now, caller, complete=complete)
    _fit(settings, bundle)
    usage = _usage(stack, caller, perimeter, problem, now, bundle)
    return RecallResult(
        usage_id=usage,
        fiches=tuple(bundle.fiches),
        common=tuple(bundle.common),
        episodes=tuple(bundle.episodes),
        lessons=tuple(bundle.lessons),
        avoid=tuple(bundle.traps),
        not_served_note=_note(caller, hits, bundle),
        estimated_tokens=_estimate(settings, bundle),
        intent=intent,
        scope=perimeter,
    )


def _hits(
    stack: Any, namespaces: tuple[str, ...], intent: str, now: Any, perimeter: Scope
) -> tuple[SearchHit, ...]:
    """The hits of the spaces the token carries, ranked as chapter 13.4 says.

    The ranking keeps every level a human may read: what an autonomous agent
    must not see is dropped later, by the tool, so that the answer can **say**
    what it withheld (EF-RES-05).
    """
    settings = stack.settings
    found: list[SearchHit] = []
    for namespace in namespaces:
        for record in stack.memory.retrieve(namespace=namespace, query=intent, top_k=settings.recall_top_k):
            found.append(hit_of(record, stack.catalog, now))
    return rank(found, scope_first=perimeter, levels=SERVED_TO_HUMAN)


def _bundle(
    stack: Any, hits: tuple[SearchHit, ...], now: Any, caller: Principal, *, complete: bool
) -> _Bundle:
    """Split the hits into what the answer carries: fiches, episodes, lessons."""
    allowed = SERVED_TO_AUTONOMOUS_AGENT if caller.autonomous else SERVED_TO_HUMAN
    levels = allowed & (COMPLETE_LEVELS if complete else RECALL_LEVELS)
    bundle = _Bundle()
    for hit in hits:
        fiche = fiche_of(stack.catalog, hit.record_id)
        if fiche is None or fiche.level not in levels:
            continue
        view = served(fiche, now, stack.settings)
        target = bundle.common if fiche.scope.is_common else bundle.fiches
        target.append(view)
    _cap_fiches(stack, bundle)
    _add_episodes(stack, hits, bundle, caller, complete=complete)
    if complete:
        _add_lessons(stack, hits, bundle, caller)
    bundle.traps = _traps(bundle)
    return bundle


def _cap_fiches(stack: Any, bundle: _Bundle) -> None:
    """At most ``max_fiches`` fiches in the answer, the perimeter first (EF-RES-02)."""
    budget = stack.settings.max_fiches
    bundle.fiches = bundle.fiches[:budget]
    bundle.common = bundle.common[: max(0, budget - len(bundle.fiches))]


def _add_episodes(
    stack: Any, hits: tuple[SearchHit, ...], bundle: _Bundle, caller: Principal, *, complete: bool
) -> None:
    """The episodes of the answer: the successful ones, and the others on demand."""
    allowed = SERVED_TO_AUTONOMOUS_AGENT if caller.autonomous else SERVED_TO_HUMAN
    successful: list[Episode] = []
    others: list[Episode] = []
    for hit in hits:
        episode = episode_of(stack.catalog, hit.record_id)
        if episode is None or episode.level not in allowed:
            continue
        if episode.outcome is Outcome.SUCCESS:
            successful.append(episode)
        else:
            others.append(episode)
    limit = stack.settings.max_episodes
    bundle.episodes = successful[:limit]
    if complete:
        bundle.episodes = [*bundle.episodes, *others[:limit]]


def _add_lessons(stack: Any, hits: tuple[SearchHit, ...], bundle: _Bundle, caller: Principal) -> None:
    """The lessons of the answer: only what the caller may read, and only there."""
    allowed = SERVED_TO_AUTONOMOUS_AGENT if caller.autonomous else SERVED_TO_HUMAN
    lessons: list[Lesson] = []
    for hit in hits:
        lesson = lesson_of(stack.catalog, hit.record_id)
        if lesson is not None and lesson.level in allowed:
            lessons.append(lesson)
    bundle.lessons = lessons[: stack.settings.max_lessons]


def _traps(bundle: _Bundle) -> list[str]:
    """The paths to avoid: what the fiches forbid, and what the episodes failed on."""
    traps: list[str] = []
    for view in (*bundle.fiches, *bundle.common):
        traps.extend(view.fiche.avoid)
    for episode in bundle.episodes:
        traps.extend(episode.failed_paths)
    seen: set[str] = set()
    return [trap for trap in traps if trap and not (trap in seen or seen.add(trap))]


def _note(caller: Principal, hits: tuple[SearchHit, ...], bundle: _Bundle) -> str:
    """What the answer says about what it did not serve (EF-RES-05, 10.2)."""
    if caller.autonomous:
        hidden = sum(1 for hit in hits if hit.level not in SERVED_TO_AUTONOMOUS_AGENT)
        if hidden:
            return f"{hidden} éléments non validés existent ; ils ne sont pas servis à un agent autonome"
        return ""
    aside = sum(1 for view in (*bundle.fiches, *bundle.common) if view.fiche.level is Level.SET_ASIDE)
    if aside:
        return f"{aside} fiche(s) sont à l'écart : leur solution a échoué deux fois de suite"
    return ""


# -- the usage row and the budget ------------------------------------------- #
def _usage(stack: Any, caller: Principal, perimeter: Scope, problem: str, now: Any, bundle: _Bundle) -> str:
    """Write the usage row: the consultation gets an identifier (EF-RET-01).

    A usage without feedback is counted apart from the rate (EF-RET-05): the
    fiche served carries one more pending answer, and the feedback takes it back.
    """
    ident = usage_identifier(caller, now, problem)
    served_ids = tuple(view.fiche.fiche_id for view in (*bundle.fiches, *bundle.common))
    usage = Usage(
        usage_id=ident,
        served_ids=served_ids,
        caller_role=caller.role,
        caller_kind=AuthorKind.HUMAN if caller.is_human else AuthorKind.AGENT,
        caller_mode=caller.mode,
        scope=perimeter,
        at=now,
        problem=problem,
    )
    quietly(stack.catalog.put_usage, usage)
    for view in (*bundle.fiches, *bundle.common):
        _count_pending(stack, view)
    return ident


def _count_pending(stack: Any, view: ServedFiche) -> None:
    """Count one usage without feedback on the fiche that was served."""
    current = stack.catalog.get_fiche(view.fiche.fiche_id)
    if current is None:
        return
    quietly(store_fiche, stack, current, no_feedback_count=current.no_feedback_count + 1)


def _fit(settings: Any, bundle: _Bundle) -> None:
    """Trim the answer until it fits in the token budget (ENF-PERF-03)."""
    while _estimate(settings, bundle) > settings.max_recall_tokens:
        if bundle.common:
            bundle.common.pop()
        elif bundle.episodes:
            bundle.episodes.pop()
        elif bundle.lessons:
            bundle.lessons.pop()
        elif bundle.fiches:
            bundle.fiches.pop()
        elif bundle.traps:
            bundle.traps.pop()
        else:
            break


def _estimate(settings: Any, bundle: _Bundle) -> int:
    """The estimated size of the answer, computed by code (ENF-IA-01)."""
    parts = [*bundle.fiches, *bundle.common, *bundle.episodes, *bundle.lessons, *bundle.traps]
    text = "\n".join(_text_of(part) for part in parts)
    return estimate_tokens(text, settings)


def _text_of(item: object) -> str:
    """The text of one element of the answer, for the size estimate."""
    if isinstance(item, ServedFiche):
        return "\n".join([item.fiche.title, item.fiche.body, *item.fiche.steps, *item.fiche.avoid])
    if isinstance(item, Episode):
        return "\n".join([item.situation, item.intent, *item.failed_paths])
    if isinstance(item, Lesson):
        return "\n".join([item.use_case, *item.advice, *item.pitfalls])
    return str(item)


# -- the degradation and the single object ----------------------------------- #
def _degraded(stack: Any, caller: Principal, client_id: str, problem: str, note: str) -> RecallResult:
    """The answer of a consultation whose memory or catalogue is down (ENF-DISP-02)."""
    now = now_of(stack)
    return RecallResult(
        usage_id=usage_identifier(caller, now, problem),
        not_served_note=f"{note} La mémoire ne répond pas : la consultation continue sans elle.",
        scope=perimeter_of(caller, client_id),
    )


def _item(stack: Any, caller: Principal, ident: str) -> dict[str, Any]:
    """One knowledge object, as a dictionary, when its perimeter allows it."""
    fiche = stack.catalog.get_fiche(ident)
    if fiche is not None:
        _check_scope(caller, fiche.scope)
        return fiche_view(fiche, stack)
    episode = stack.catalog.get_episode(ident)
    if episode is not None:
        _check_scope(caller, episode.scope)
        return episode_view(episode)
    lesson = stack.catalog.get_lesson(ident)
    if lesson is not None:
        _check_scope(caller, lesson.scope)
        return lesson_view(lesson)
    raise NotFound(f"objet inconnu : {ident}", id=ident)


def _check_scope(caller: Principal, scope: Scope) -> None:
    """Refuse an object whose perimeter the caller does not carry."""
    allowed = {perimeter.actor_id for perimeter in caller.allowed_scopes()}
    if scope.actor_id in allowed:
        return
    raise ValidationError(
        f"objet hors du périmètre du caller : {scope.actor_id}",
        scope=scope.actor_id,
    )


__all__ = ["COMPLETE_LEVELS", "RECALL_LEVELS", "get_item", "recall", "recall_all"]
