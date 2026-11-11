"""The episode row the capture writes in the catalogue (chapter 14).

AgentCore Memory distils the episode: :mod:`sillage.memory.distill` will later
write the same row again, enriched with a situation, teachings and failed paths
read by a model. The capture writes the **exact state** it knows, computed by
code (ENF-IA-01): the deterministic episode identifier, the turns of the work,
the outcome read in the closing event, and - when a human judged the work of an
agent - the rejected diagnosis, the reason and who corrected it (EF-CAP-10).

Because the identifier is ``episode_id(scope, work item)``, the distilled
episode replaces this row instead of creating a second one: one finished job,
one episode (R3).

Nothing here is a figure about a person: the author stays a pseudonym, and no
function groups episodes by author (ENF-RH-01).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from sillage.capture.authorship import VerdictKind, is_verdict_event, strip_label
from sillage.kernel.ids import episode_id
from sillage.kernel.types import Episode, Level, Outcome, Trace, Turn

#: Words of a closing event that tell the work failed.
FAILURE_WORDS = ("sans résolution", "échec", "non résolu", "abandonné")

#: Words of a closing event that tell the work succeeded.
SUCCESS_WORDS = ("clos", "close", "fusionnée", "terminé", "résolu", "succès")

#: Words of a closing event that tell the work half succeeded.
MIXED_WORDS = ("mitigée", "partiellement")


def episode_from_trace(
    trace: Trace, *, memory_record_id: str = "", created_at: datetime | None = None
) -> Episode:
    """The catalogue episode of a captured job, before the distillation."""
    origin = trace.origin
    return Episode(
        episode_id=episode_id(trace.scope.actor_id, trace.work_item_id),
        scope=trace.scope,
        work_item_id=trace.work_item_id,
        situation=situation_of(trace),
        intent=intent_of(trace),
        turns=turns_of(trace),
        outcome=outcome_of(trace),
        justification=origin.get("raisonVerdict", ""),
        memory_record_id=memory_record_id or None,
        source=trace.source,
        author_kind=trace.author_kind,
        author=trace.author,
        technologies=trace.technologies,
        level=Level.RAW,
        created_at=created_at,
        rejected_diagnosis=origin.get("diagnosticRejete", ""),
        corrected_by=corrected_by(origin),
    )


#: Verdicts that mean a human took over the work of an agent.
CORRECTING_VERDICTS = frozenset({VerdictKind.REJECTED.value, VerdictKind.CORRECTED.value})


def corrected_by(origin: dict[str, str]) -> str:
    """Who corrected the agent, and nobody when the work was simply accepted."""
    if origin.get("verdict", "") not in CORRECTING_VERDICTS:
        return ""
    return origin.get("verdictPar", "")


def situation_of(trace: Trace) -> str:
    """The situation: what was asked, without its author label."""
    for event in trace.events:
        if event.kind == "request":
            return strip_label(event.text)
    return strip_label(trace.events[0].text) if trace.events else ""


def intent_of(trace: Trace) -> str:
    """The intention: what the one who resolved tried first."""
    for event in trace.events:
        if event.kind == "action":
            return strip_label(event.text)
    return situation_of(trace)


def turns_of(trace: Trace) -> tuple[Turn, ...]:
    """One turn per action, with the tool output that answered it.

    The verdict of a human on the work of an agent is a turn too: the episode
    keeps the rejected diagnosis, the reason and the right one (R11).
    """
    turns: list[Turn] = []
    for event in trace.events:
        if event.kind == "action" or is_verdict_event(event):
            turns.append(Turn(action=strip_label(event.text)))
        elif event.kind == "tool_output" and turns:
            turns[-1] = replace(turns[-1], result=strip_label(event.text))
    return tuple(turns)


def outcome_of(trace: Trace) -> Outcome:
    """The outcome, read by the code in the closing event - never guessed."""
    closing = _closing_text(trace)
    if not closing:
        return Outcome.UNKNOWN
    lowered = closing.lower()
    if any(word in lowered for word in FAILURE_WORDS):
        return Outcome.FAILURE
    if any(word in lowered for word in MIXED_WORDS):
        return Outcome.MIXED
    if any(word in lowered for word in SUCCESS_WORDS):
        return Outcome.SUCCESS
    return Outcome.UNKNOWN


def _closing_text(trace: Trace) -> str:
    """The text of the last system event, empty when the trace has none."""
    for event in reversed(trace.events):
        if event.kind == "system" and not is_verdict_event(event):
            return event.text
    return ""


__all__ = [
    "CORRECTING_VERDICTS",
    "FAILURE_WORDS",
    "MIXED_WORDS",
    "SUCCESS_WORDS",
    "corrected_by",
    "episode_from_trace",
    "intent_of",
    "outcome_of",
    "situation_of",
    "turns_of",
]
