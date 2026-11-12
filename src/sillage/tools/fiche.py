"""The fiche: a lesson a human read, corrected and validated (chapter 10.2).

Three tools share this module, because they share the same object:

* ``propose_fiche`` writes the **draft** - a writer agent proposes it, a human
  decides (EF-VAL-01);
* ``validate_fiche`` is the **decision** of the referent: validate, correct or
  refuse (EF-VAL-03). A fiche whose statement cites no episode is refused by a
  control of the code (EF-VAL-02);
* ``set_aside`` takes a fiche **out of service** when it failed twice (EF-VAL-06).

The state machine of :mod:`sillage.catalog.levels` is the only one that says
which level a fiche may reach: a draft becomes validated, a validated fiche
becomes proven by usage, a failed one goes aside. The tools apply it, they do
not reimplement it.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from sillage.catalog.levels import can_transition
from sillage.guards.sources import check_claims
from sillage.kernel.errors import NotFound, PolicyDenied, SillageError, ValidationError
from sillage.kernel.ids import fiche_id as make_fiche_id
from sillage.kernel.protocols import Principal
from sillage.kernel.types import Claim, Episode, Fiche, Lesson, Level, Scope
from sillage.memory.namespaces import fiche_namespace
from sillage.tools.errors import quietly, tool_error
from sillage.tools.gateway import (
    caller_of,
    create_fiche,
    enforce,
    now_of,
    perimeter_of,
    store_fiche,
    writer_of,
)
from sillage.tools.texts import fiche_record

#: The three decisions a referent may take on a fiche (EF-VAL-03).
DECISIONS: dict[str, str] = {
    "validate": "validate",
    "accept": "validate",
    "valider": "validate",
    "valide": "validate",
    "correct": "correct",
    "corriger": "correct",
    "corrige": "correct",
    "edit": "correct",
    "refuse": "refuse",
    "reject": "refuse",
    "refuser": "refuse",
    "withdraw": "refuse",
    "retire": "refuse",
    "retirer": "refuse",
}

#: The fields a draft may carry, with the spellings the clients use.
DRAFT_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("title", "titre"),
    "body": ("body", "texte"),
    "steps": ("steps", "etapes", "étapes"),
    "avoid": ("avoid", "aEviter", "àEviter"),
    "validity": ("validity", "validite", "validité"),
    "provenance": ("provenance", "origine"),
    "technologies": ("technologies", "technos", "technologies"),
}


def propose_fiche(
    stack: Any,
    *,
    lesson_id: str = "",
    episode_ids: Any = (),
    draft: dict[str, Any] | None = None,
    principal: Principal | None = None,
) -> Fiche | dict[str, Any]:
    """A draft fiche, waiting for a referent.

    Humans and assisted agents may propose one; an autonomous agent may not
    (rule M.1): a proposal waits for a human relecture.
    """
    caller = caller_of(principal)
    try:
        enforce(
            stack,
            "propose_fiche",
            caller,
            lessonId=lesson_id,
            episodeIds=list(episode_ids or ()),
        )
    except PolicyDenied as exc:
        return tool_error(exc)
    try:
        return _propose(stack, caller, lesson_id=lesson_id, episode_ids=episode_ids, draft=draft)
    except SillageError as exc:
        return tool_error(exc)


def validate_fiche(
    stack: Any,
    *,
    fiche_id: str,
    decision: str,
    edits: dict[str, Any] | None = None,
    principal: Principal | None = None,
) -> Fiche | dict[str, Any]:
    """The decision of the referent on a fiche: validate, correct or refuse.

    Only a referent may call it (rule M.2, R7). A fiche holding a statement
    that cites no episode is refused by a control of the code (EF-VAL-02).
    """
    caller = caller_of(principal)
    try:
        enforce(stack, "validate_fiche", caller, ficheId=fiche_id, decision=decision)
    except PolicyDenied as exc:
        return tool_error(exc)
    try:
        return _validate(stack, caller, fiche_id=fiche_id, decision=decision, edits=edits)
    except SillageError as exc:
        return tool_error(exc)


def set_aside(
    stack: Any,
    *,
    fiche_id: str,
    reason: str,
    principal: Principal | None = None,
) -> Fiche | dict[str, Any]:
    """Take a fiche out of service: it is no longer served to the agents.

    A referent or an administrator may do it (rule M.3). The skill exported from
    that fiche is deprecated in the registry at the same time (EF-EXE-04).
    """
    caller = caller_of(principal)
    try:
        enforce(stack, "set_aside", caller, ficheId=fiche_id, reason=reason)
    except PolicyDenied as exc:
        return tool_error(exc)
    try:
        return _set_aside(stack, fiche_id=fiche_id, reason=reason)
    except SillageError as exc:
        return tool_error(exc)


# -- the draft --------------------------------------------------------------- #
def _propose(
    stack: Any,
    caller: Principal,
    *,
    lesson_id: str,
    episode_ids: Any,
    draft: dict[str, Any] | None,
) -> Fiche:
    """Build the draft from the lesson, the episodes and what the writer sent."""
    content = dict(draft or {})
    lesson = _lesson(stack, lesson_id)
    episodes = _episodes(stack, episode_ids)
    if lesson is not None:
        episodes = episodes or [
            episode for episode in stack.catalog.list_episodes() if episode.episode_id in lesson.episode_ids
        ]
    if not lesson and not episodes:
        raise ValidationError(
            "une proposition de fiche cite au moins une leçon ou un épisode",
            lessonId=lesson_id,
        )
    perimeter = _perimeter_of(lesson, episodes, caller)
    title = str(_field(content, "title") or (lesson.use_case if lesson else "") or "Sans titre")
    sources = tuple(episode.work_item_id for episode in episodes)
    fiche = Fiche(
        fiche_id=make_fiche_id(perimeter.actor_id, title),
        scope=perimeter,
        title=title,
        body=str(_field(content, "body")),
        steps=_lines(content, "steps"),
        avoid=_lines(content, "avoid"),
        validity=str(_field(content, "validity")),
        claims=_claims(content, episodes),
        sources=sources,
        level=Level.PROPOSED,
        provenance=str(_field(content, "provenance")) or _provenance(lesson, episodes),
        technologies=tuple(_field(content, "technologies") or ()),
        lesson_id=lesson.lesson_id if lesson else "",
        memory_record_id=make_fiche_id(perimeter.actor_id, title),
    )
    _publish(stack, fiche)
    return create_fiche(stack, fiche)


def _lesson(stack: Any, lesson_id: str) -> Lesson | None:
    """The lesson the proposal is built on, when one is named."""
    if not lesson_id:
        return None
    lesson = stack.catalog.get_lesson(lesson_id)
    if lesson is None:
        raise NotFound(f"leçon inconnue : {lesson_id}", lessonId=lesson_id)
    return lesson


def _episodes(stack: Any, episode_ids: Any) -> list[Episode]:
    """The episodes the proposal cites, when they are named."""
    found: list[Episode] = []
    for episode_id in episode_ids or ():
        episode = stack.catalog.get_episode(str(episode_id))
        if episode is None:
            raise NotFound(f"épisode inconnu : {episode_id}", episodeId=str(episode_id))
        found.append(episode)
    return found


def _perimeter_of(lesson: Lesson | None, episodes: list[Episode], caller: Principal) -> Scope:
    """The perimeter of the fiche: the one of its sources, or the one of the token."""
    if lesson is not None:
        return lesson.scope
    if episodes:
        return episodes[0].scope
    home = caller.home_scope()
    return home if home is not None else perimeter_of(caller)


def _claims(content: dict[str, Any], episodes: list[Episode]) -> tuple[Claim, ...]:
    """The statements of the draft, each one bound to the episodes it comes from."""
    raw = content.get("claims") or content.get("affirmations") or ()
    claims: list[Claim] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("texte") or "")
        ids = item.get("episodeIds") or item.get("episodes") or item.get("sources") or ()
        if not text:
            continue
        claims.append(Claim(text=text, episode_ids=tuple(str(one) for one in ids)))
    if claims:
        return tuple(claims)
    return tuple(
        Claim(text=episode.intent or episode.situation, episode_ids=(episode.episode_id,))
        for episode in episodes
    )


def _provenance(lesson: Lesson | None, episodes: list[Episode]) -> str:
    """Where the draft comes from, in one sentence, without naming anybody."""
    kinds = {episode.author_kind.value for episode in episodes}
    if lesson is not None and not kinds:
        return "une leçon de l'équipe"
    if len(kinds) > 1:
        return "des épisodes d'ingénieurs et d'agents"
    if kinds:
        return "des épisodes d'agents" if kinds == {"agent"} else "des épisodes d'ingénieurs"
    return ""


# -- the decision ------------------------------------------------------------ #
def _validate(
    stack: Any,
    caller: Principal,
    *,
    fiche_id: str,
    decision: str,
    edits: dict[str, Any] | None,
) -> Fiche:
    """Apply the decision of the referent to a fiche."""
    fiche = stack.catalog.get_fiche(fiche_id)
    if fiche is None:
        raise NotFound(f"fiche inconnue : {fiche_id}", ficheId=fiche_id)
    wanted = _decision_of(decision)
    if wanted == "refuse":
        return _refuse(stack, fiche)
    corrected = _corrected(fiche, edits)
    check_claims(corrected)
    target = Level.VALIDATED
    if wanted == "correct":
        target = fiche.level
    elif not can_transition(fiche.level, target):
        raise ValidationError(
            f"une fiche de niveau {fiche.level.value} ne peut pas devenir validée",
            ficheId=fiche_id,
            level=fiche.level.value,
        )
    stored = store_fiche(
        stack,
        corrected,
        level=target,
        validated_by=writer_of(caller),
        validated_at=now_of(stack),
        memory_record_id=fiche.fiche_id,
    )
    _publish(stack, stored)
    return stored


def _refuse(stack: Any, fiche: Fiche) -> Fiche:
    """Withdraw a fiche the referent refuses."""
    if not can_transition(fiche.level, Level.WITHDRAWN):
        raise ValidationError(
            f"une fiche de niveau {fiche.level.value} ne peut pas être retirée : mettez-la d'abord à l'écart",
            ficheId=fiche.fiche_id,
            level=fiche.level.value,
        )
    return store_fiche(stack, fiche, level=Level.WITHDRAWN)


def _decision_of(decision: str) -> str:
    """The decision the caller means, in French or in English."""
    wanted = DECISIONS.get(str(decision or "").strip().lower())
    if wanted is None:
        raise ValidationError(
            f"décision inconnue : {decision!r} (attendu : valider, corriger, refuser)",
            decision=str(decision or ""),
        )
    return wanted


def _corrected(fiche: Fiche, edits: dict[str, Any] | None) -> Fiche:
    """The fiche as the referent corrected it."""
    content = dict(edits or {})
    if not content:
        return fiche
    changes: dict[str, Any] = {}
    for field, names in DRAFT_ALIASES.items():
        if any(name in content for name in names):
            value = _field(content, field)
            changes[field] = tuple(value) if field in {"steps", "avoid", "technologies"} else value
    if "claims" in content or "affirmations" in content:
        changes["claims"] = _claims(content, [])
    return fiche if not changes else _replace(fiche, changes)


def _replace(fiche: Fiche, changes: dict[str, Any]) -> Fiche:
    """The fiche with the corrections of the referent applied."""
    return replace(fiche, **changes)


# -- the end of service ------------------------------------------------------ #
def _set_aside(stack: Any, *, fiche_id: str, reason: str) -> Fiche:
    """Take a fiche out of service, and deprecate the skill it produced."""
    fiche = stack.catalog.get_fiche(fiche_id)
    if fiche is None:
        raise NotFound(f"fiche inconnue : {fiche_id}", ficheId=fiche_id)
    if not can_transition(fiche.level, Level.SET_ASIDE):
        raise ValidationError(
            f"une fiche de niveau {fiche.level.value} ne peut pas être mise à l'écart",
            ficheId=fiche_id,
            level=fiche.level.value,
        )
    stored = store_fiche(stack, fiche, level=Level.SET_ASIDE, set_aside_reason=reason)
    _deprecate_skill(stack, stored, reason)
    return stored


def _deprecate_skill(stack: Any, fiche: Fiche, reason: str) -> None:
    """Deprecate the skill exported from that fiche (EF-EXE-04)."""
    from sillage.tools.texts import name_of

    registry = stack.registry
    skills = getattr(registry, "skills", None)
    if not callable(skills):
        return
    for record in skills():
        if getattr(record, "name", "") != name_of(fiche):
            continue
        try:
            registry.deprecate(record.record_id, reason)
        except Exception:  # a registry that cannot answer must not break the tool
            return


# -- the shared helpers ------------------------------------------------------ #
def _field(content: dict[str, Any], name: str) -> Any:
    """One field of a draft, whatever the spelling the client used."""
    for alias in DRAFT_ALIASES.get(name, (name,)):
        value = content.get(alias)
        if value not in (None, "", [], ()):
            return value
    return ""


def _lines(content: dict[str, Any], name: str) -> tuple[str, ...]:
    """One list field of a draft, as a tuple of lines."""
    value = _field(content, name)
    if isinstance(value, str):
        return tuple(line.strip() for line in value.splitlines() if line.strip())
    return tuple(str(line) for line in value if line)


def _publish(stack: Any, fiche: Fiche) -> None:
    """Write the text of the fiche in Memory, so that a search finds it."""
    namespace = fiche_namespace(stack.settings, fiche.scope)
    record = fiche_record(fiche, namespace=namespace, created_at=now_of(stack))
    quietly(stack.memory.put_records, [record])


__all__ = ["DECISIONS", "DRAFT_ALIASES", "propose_fiche", "set_aside", "validate_fiche"]
