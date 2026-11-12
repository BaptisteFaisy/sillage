"""The forgetting of one work (EF-ENT-03, R19).

Chapter 9.8 asks for two different deletions, and the catalogue owns both:

* the end of a contract deletes a whole perimeter - that is
  ``CatalogStore.delete_scope``, and it belongs to the administration;
* the forgetting of **one work** deletes its episode and **signals** the fiches
  that relied on it: those fiches are kept, because a referent decides what
  happens to them (EF-ENT-02).

This tool implements the second one. The cascade of a single work item is:

    work -> episode -> the lessons built on that episode
          -> the fiches that cited the work are flagged, never deleted

The permission is decided by layer 1 before anything is removed: the
administrator may forget anything, and an engineer only their own work - the
tool resolves the author of the work in the catalogue and passes its pseudonym
to the policy engine, which never reads the catalogue itself.
"""

from __future__ import annotations

from typing import Any

from sillage.capture.dedupe import raw_key
from sillage.catalog.cascade import dependent_fiches
from sillage.kernel.errors import NotFound, PolicyDenied, SillageError, ValidationError
from sillage.kernel.ids import episode_id
from sillage.kernel.protocols import Principal
from sillage.kernel.types import Fiche, Lesson, Scope
from sillage.tools.errors import quietly, tool_error
from sillage.tools.gateway import caller_of, enforce


def forget(
    stack: Any,
    *,
    work_item_id: str,
    principal: Principal | None = None,
) -> dict[str, Any]:
    """Forget one work: its episode goes, the fiches that used it are flagged.

    An administrator, or the engineer who did the work (table 10.5). A refusal
    is returned, never raised.
    """
    caller = caller_of(principal)
    author = _author_of(stack, work_item_id)
    try:
        enforce(stack, "forget", caller, workItemId=work_item_id, author=author)
    except PolicyDenied as exc:
        return tool_error(exc)
    try:
        return _forget(stack, work_item_id=work_item_id)
    except SillageError as exc:
        return tool_error(exc)


def _author_of(stack: Any, work_item_id: str) -> str:
    """The pseudonym of the author of the work, for the policy engine.

    The engine compares it with the pseudonym of the caller: the tool resolves
    the author in the catalogue, and the engine never reads the catalogue.
    """
    trace = stack.catalog.get_trace(work_item_id)
    return trace.author if trace is not None else ""


def _forget(stack: Any, *, work_item_id: str) -> dict[str, Any]:
    """Run the cascade of one work item and report what it touched."""
    trace = stack.catalog.get_trace(work_item_id)
    if trace is None:
        raise NotFound(f"travail inconnu : {work_item_id}", workItemId=work_item_id)
    scope = trace.scope
    ident = episode_id(scope.actor_id, work_item_id)
    lessons = _lessons(stack, ident)
    flagged = _flagged(stack, work_item_id)
    removed = _erase(stack, work_item_id=work_item_id, episode_ident=ident, lessons=lessons)
    _forget_memory(stack, scope, work_item_id, trace)
    _keep_flagged(stack, flagged)
    return {
        "workItemId": work_item_id,
        "episodeId": ident,
        "supprime": removed,
        "fichesSignalees": [fiche.fiche_id for fiche in flagged],
        "message": (
            "Travail oublié : son épisode a disparu, les fiches qui s'appuyaient dessus "
            "sont signalées au référent."
        ),
    }


def _lessons(stack: Any, ident: str) -> list[Lesson]:
    """The lessons built on the episode that is about to disappear."""
    return [lesson for lesson in stack.catalog.list_lessons() if ident in lesson.episode_ids]


def _flagged(stack: Any, work_item_id: str) -> list[Fiche]:
    """The fiches that cited the work: they are kept, and reported."""
    fiches = tuple(stack.catalog.list_fiches())
    return list(dependent_fiches(fiches, frozenset(), frozenset({work_item_id})))


def _erase(stack: Any, *, work_item_id: str, episode_ident: str, lessons: list[Lesson]) -> dict[str, int]:
    """Remove the rows of the work from the catalogue.

    The catalogue port of chapter 14 only knows how to delete a whole
    perimeter; the deletion of one work therefore acts on the rows the
    catalogue exposes. A catalogue that does not expose them refuses the
    operation rather than pretending it deleted something.
    """
    catalog = stack.catalog
    traces = getattr(catalog, "traces", None)
    episodes = getattr(catalog, "episodes", None)
    if not isinstance(traces, dict) or not isinstance(episodes, dict):
        raise ValidationError(
            "ce catalogue ne sait pas oublier un travail isolé : "
            "seule la suppression d'un périmètre est disponible",
            workItemId=work_item_id,
        )
    removed = {"travaux": 0, "episodes": 0, "lecons": 0}
    if traces.pop(work_item_id, None) is not None:
        removed["travaux"] = 1
    if episodes.pop(episode_ident, None) is not None:
        removed["episodes"] = 1
    known = getattr(catalog, "lessons", None)
    if isinstance(known, dict):
        for lesson in lessons:
            if known.pop(lesson.lesson_id, None) is not None:
                removed["lecons"] += 1
    _forget_fingerprints(catalog, work_item_id)
    return removed


def _forget_fingerprints(catalog: Any, work_item_id: str) -> None:
    """The dedupe index forgets the work: a re-capture is not a duplicate."""
    fingerprints = getattr(catalog, "fingerprints", None)
    if not isinstance(fingerprints, dict):
        return
    for fingerprint, work in list(fingerprints.items()):
        if work == work_item_id:
            fingerprints.pop(fingerprint, None)


def _forget_memory(stack: Any, scope: Scope, work_item_id: str, trace: Any) -> None:
    """Erase the memory of the job: the events and the records of its session."""
    quietly(stack.memory.delete_records, actor_id=scope.actor_id, session_id=work_item_id)
    key = trace.origin.get("rawKey") or raw_key(trace)
    quietly(stack.blobs.delete, key)


def _keep_flagged(stack: Any, flagged: list[Fiche]) -> None:
    """Leave the flagged fiches in the catalogue, and mark them as such."""
    if hasattr(stack.catalog, "flagged"):
        stack.catalog.flagged = tuple(flagged)


__all__ = ["forget"]
