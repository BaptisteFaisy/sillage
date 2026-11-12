"""The usage feedback: what the consumer says about what it was served.

EF-RET-02 asks the tool to record three answers - it helped, it did not help,
it was wrong - and EF-RET-04 asks the rate to be computed by the code. Both
happen here, and both happen in the only place that may: the level machine of
:mod:`sillage.catalog.levels`.

The state machine decides, this tool applies:

* a positive answer confirms the fiche and may promote it to "éprouvée" after
  three successes by two distinct authors (EF-VAL-05);
* two negative answers in a row set the fiche aside (EF-VAL-06), and **the
  referent is warned**, with the two consultations in cause (8.6);
* a usage without an answer never enters the rate: it is counted apart
  (EF-RET-05).
"""

from __future__ import annotations

from typing import Any

from sillage.catalog.levels import apply_feedback
from sillage.kernel.errors import NotFound, PolicyDenied, SillageError
from sillage.kernel.protocols import Principal
from sillage.kernel.types import LEVEL_CODE, Feedback, FeedbackResult, Fiche, Level
from sillage.tools.errors import tool_error
from sillage.tools.gateway import (
    caller_of,
    enforce,
    feedback_result_of,
    now_of,
    store_fiche,
    writer_of,
)


def report_outcome(
    stack: Any,
    *,
    usage_id: str,
    result: str,
    note: str = "",
    principal: Principal | None = None,
) -> dict[str, Any]:
    """What the consumer says about the knowledge it was served.

    Everybody may call it (table 10.5). A refusal is returned, never raised.
    """
    caller = caller_of(principal)
    try:
        enforce(stack, "report_outcome", caller, usageId=usage_id, result=result)
    except PolicyDenied as exc:
        return tool_error(exc)
    try:
        return _report(stack, caller, usage_id=usage_id, result=result, note=note)
    except SillageError as exc:
        return tool_error(exc)


def _report(stack: Any, caller: Principal, *, usage_id: str, result: str, note: str) -> dict[str, Any]:
    """Record the answer, then let the level machine move the fiches."""
    outcome = feedback_result_of(result)
    usage = stack.catalog.get_usage(usage_id)
    if usage is None:
        raise NotFound(f"usage inconnu : {usage_id}", usageId=usage_id)
    now = now_of(stack)
    author = writer_of(caller)
    targets = _targets(stack, usage.served_ids)
    stack.catalog.put_feedback(
        Feedback(
            usage_id=usage_id,
            result=outcome,
            note=note,
            at=now,
            fiche_id=targets[0].fiche_id if targets else "",
            caller=author,
        )
    )
    updated = [_apply(stack, target, outcome, now, author) for target in targets]
    for before, after in zip(targets, updated, strict=True):
        if after.level is Level.SET_ASIDE and before.level is not Level.SET_ASIDE:
            _warn_referent(stack, after)
    return {
        "usageId": usage_id,
        "result": outcome.value,
        "fiches": [fiche.fiche_id for fiche in updated],
        "niveaux": {fiche.fiche_id: LEVEL_CODE[fiche.level] for fiche in updated},
        "message": _message(updated),
    }


def _targets(stack: Any, served_ids: tuple[str, ...]) -> list[Fiche]:
    """The fiches the usage served, as the catalogue holds them now."""
    found: list[Fiche] = []
    for fiche_id in served_ids:
        fiche = stack.catalog.get_fiche(fiche_id)
        if fiche is not None:
            found.append(fiche)
    return found


def _apply(stack: Any, fiche: Fiche, outcome: FeedbackResult, now: Any, author: str) -> Fiche:
    """Move one fiche with the level machine, and write the new version."""
    moved = apply_feedback(fiche, outcome, settings=stack.settings, now=now, author=author)
    return store_fiche(stack, moved)


def _warn_referent(stack: Any, fiche: Fiche) -> None:
    """Warn the referent when a fiche has just been set aside (EF-VAL-06)."""
    if fiche.level is not Level.SET_ASIDE:
        return
    causes = _causes(stack, fiche)
    body = (
        f"La fiche {fiche.fiche_id} « {fiche.title} » a reçu deux retours négatifs de suite : "
        f"elle passe à l'écart et n'est plus servie aux agents. "
        f"Consultations en cause : {', '.join(causes) or 'inconnues'}. "
        f"Niveau : {LEVEL_CODE[fiche.level]}. Motif : {fiche.set_aside_reason}."
    )
    stack.notifier.notify(
        referent=fiche.validated_by or "référent",
        subject="Fiche mise à l'écart",
        body=body,
        fiche_id=fiche.fiche_id,
    )


def _causes(stack: Any, fiche: Fiche) -> list[str]:
    """The consultations that brought the fiche down, the last two."""
    answers = [
        answer
        for answer in stack.catalog.list_feedback(fiche.fiche_id)
        if answer.result is not FeedbackResult.HELPED
    ]
    causes: list[str] = []
    for answer in answers[-2:]:
        usage = stack.catalog.get_usage(answer.usage_id)
        problem = usage.problem if usage is not None else ""
        causes.append(f"{answer.usage_id} ({problem})" if problem else answer.usage_id)
    return causes


def _message(updated: list[Fiche]) -> str:
    """The sentence the caller reads: what the answer changed."""
    if not updated:
        return "Retour enregistré : aucune fiche n'avait été servie lors de cette consultation."
    aside = [fiche.fiche_id for fiche in updated if fiche.level is Level.SET_ASIDE]
    if aside:
        return f"Retour enregistré. Fiche(s) mise(s) à l'écart : {', '.join(aside)}. Le référent est prévenu."
    return "Retour enregistré : le niveau des fiches servies a été mis à jour."


__all__ = ["report_outcome"]
