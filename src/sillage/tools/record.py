"""The declared episode: an agent tells what it did (EF-CAP-06, 13.2).

Chapter 13.2 lists three ways to capture the work of an agent; this one is the
declaration: the agent calls ``record_episode`` with the structure the
specification imposes, and Sillage builds the episode from it.

The structure is checked **by the code**, before anything is stored: the
capture module already knows what a declared episode looks like
(:func:`sillage.capture.sources.declared_episode`), so this tool does not
invent a second shape - it prepares the payload and lets the capture do the
work, redaction, deduplication and distillation included.

Two consequences fall out of that choice:

* the perimeter of the episode is the one the **token** carries, never the one
  the payload names (10.4);
* the same declaration twice gives **one** episode (EF-CAP-11, R3): the capture
  recognises the trace it already received and says so.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sillage.capture.sources import declared_episode
from sillage.kernel.errors import PolicyDenied, SillageError, ValidationError
from sillage.kernel.protocols import Principal
from sillage.tools.errors import tool_error
from sillage.tools.gateway import caller_of, enforce, perimeter_of

#: The outcomes a declaring agent may announce (EF-CAP-06).
DECLARED_OUTCOMES: tuple[str, ...] = ("success", "failure", "mixed", "unknown")


def record_episode(
    stack: Any,
    *,
    client_id: str,
    work_item_id: str,
    situation: str,
    intent: str,
    attempts: Any = (),
    outcome: str = "",
    evidence: Any = (),
    principal: Principal | None = None,
) -> dict[str, Any]:
    """One episode, declared by the agent that did the work.

    Everybody may call it, each one for its own client (table 10.5). A refusal
    is returned, never raised.
    """
    caller = caller_of(principal, client_id=client_id)
    try:
        enforce(
            stack,
            "record_episode",
            caller,
            clientId=client_id,
            workItemId=work_item_id,
            outcome=outcome,
        )
    except PolicyDenied as exc:
        return tool_error(exc)
    try:
        return _declare(
            stack,
            caller,
            client_id=client_id,
            work_item_id=work_item_id,
            situation=situation,
            intent=intent,
            attempts=attempts,
            outcome=outcome,
            evidence=evidence,
        )
    except SillageError as exc:
        return tool_error(exc)


def _declare(
    stack: Any,
    caller: Principal,
    *,
    client_id: str,
    work_item_id: str,
    situation: str,
    intent: str,
    attempts: Any,
    outcome: str,
    evidence: Any,
) -> dict[str, Any]:
    """Build the payload of the declaration, then let the capture do its part."""
    perimeter = perimeter_of(caller, client_id)
    payload = {
        "scope": {"kind": perimeter.kind.value, "client_id": perimeter.client_id},
        "workItemId": work_item_id,
        "situation": situation,
        "intent": intent,
        "attempts": _attempts(attempts),
        "outcome": _outcome(outcome),
        "evidence": _evidence(evidence),
        "author": caller.subject,
    }
    trace = declared_episode(payload, stack.model)
    report = stack.ingest.ingest_trace(trace)
    message = (
        "Épisode déclaré : il rejoint la mémoire du client."
        if not report.duplicate
        else "Cette trace avait déjà été reçue : un seul épisode la décrit."
    )
    return {
        "episodeId": report.episode_id,
        "workItemId": trace.work_item_id,
        "duplicate": report.duplicate,
        "eventsCreated": report.events_created,
        "niveau": "0",
        "message": message,
    }


def _attempts(attempts: Any) -> list[dict[str, str]]:
    """The attempts of the declaration: a sentence, or a full step."""
    steps: list[dict[str, str]] = []
    for attempt in attempts or ():
        if isinstance(attempt, Mapping):
            steps.append({str(key): str(value) for key, value in attempt.items() if value})
        elif attempt:
            steps.append({"action": str(attempt)})
    return steps


def _evidence(evidence: Any) -> str:
    """The evidence the agent quotes: the lines it wants kept."""
    if isinstance(evidence, str):
        return evidence
    lines = [str(line) for line in (evidence or ()) if line]
    return "\n".join(lines)


def _outcome(outcome: str) -> str:
    """The declared outcome, checked by code before anything is stored."""
    value = str(outcome or "").strip().lower()
    if not value:
        return "unknown"
    if value not in DECLARED_OUTCOMES:
        raise ValidationError(
            f"issue déclarée inconnue : {outcome!r} (attendu : {', '.join(DECLARED_OUTCOMES)})",
            outcome=value,
        )
    return value


__all__ = ["DECLARED_OUTCOMES", "record_episode"]
