"""Who did the work: author kind, share of each, verdict of a human.

The specification asks for three things this module owns:

* the label written between brackets in front of every element of a trace
  (13.1): ``[ingénieur]``, ``[agent d'exploitation]``, ``[agent de code]``,
  ``[système]``;
* the kind of author - human, agent or mixed - and the **share of each** when
  an agent started and a human finished (EF-CAP-09): one single episode, with
  the part of everybody in it;
* the verdict of a human on the work of an agent - accepted, corrected or
  rejected - kept with the episode (EF-CAP-10, journey 8.4).

Authors are pseudonyms (``kernel.ids.pseudonym``): no figure about a person
is ever computed, and none can be (ENF-RH-01).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sillage.capture.clock import parse_moment
from sillage.kernel.ids import pseudonym
from sillage.kernel.types import AuthorKind, Role, Source, TraceEvent

#: Label of a human author (13.1).
LABEL_HUMAN = "[ingénieur]"
#: Label of an exploitation agent.
LABEL_AGENT_EXPLOITATION = "[agent d'exploitation]"
#: Label of a code agent.
LABEL_AGENT_CODE = "[agent de code]"
#: Label of the system: closing event, verdict, state change.
LABEL_SYSTEM = "[système]"
#: Label of the automatic alarm that opens an incident.
LABEL_ALARM = "[alarme]"
#: Label of a tool output: a command, a log extract, a test result.
LABEL_COMMAND = "[commande]"

#: Sources whose agent is a code agent rather than an exploitation agent.
CODE_SOURCES = frozenset({Source.KIRO, Source.MERGE})

#: Markers telling that a system event carries a human verdict.
VERDICT_MARKERS = ("rejeté par", "corrigé par", "accepté par")


class VerdictKind(StrEnum):
    """What a human says about the work of an agent (EF-CAP-10)."""

    ACCEPTED = "accepted"
    CORRECTED = "corrected"
    REJECTED = "rejected"


def label_for(author_kind: AuthorKind, source: Source) -> str:
    """The bracket label of an author, given its kind and the source."""
    if author_kind is AuthorKind.HUMAN:
        return LABEL_HUMAN
    if author_kind is AuthorKind.AGENT:
        return LABEL_AGENT_CODE if source in CODE_SOURCES else LABEL_AGENT_EXPLOITATION
    return LABEL_HUMAN


def event_text(label: str, text: str) -> str:
    """Put the author label in front of the text of an element (13.1)."""
    body = (text or "").strip()
    if not body:
        return label
    if body.startswith("["):
        return body
    return f"{label} {body}"


def sentence(text: str) -> str:
    """One sentence of business text: a single full stop, never two."""
    body = (text or "").strip()
    if not body:
        return ""
    return body if body.endswith((".", "!", "?")) else f"{body}."


def strip_label(text: str) -> str:
    """The text of an element without its leading label."""
    body = (text or "").strip()
    if body.startswith("["):
        _, _, rest = body.partition("]")
        return rest.strip() or body
    return body


@dataclass(frozen=True)
class HumanVerdict:
    """The verdict of a human on the work of an agent (EF-CAP-10).

    ``rejected_diagnosis`` and ``correct_diagnosis`` are what makes the episode
    useful: an agent that concluded "the database is saturated" and an
    engineer who answered "no, it is a connection leak" must both stay in the
    episode (journey 8.4), otherwise the next agent repeats the mistake.
    """

    kind: VerdictKind
    reviewer: str
    reason: str = ""
    rejected_diagnosis: str = ""
    correct_diagnosis: str = ""
    at: datetime | None = None

    def event(self) -> TraceEvent:
        """The system event that carries the verdict into the trace."""
        return TraceEvent(
            role=Role.OTHER,
            text=event_text(LABEL_SYSTEM, self.body()),
            author=pseudonym(self.reviewer),
            author_kind=AuthorKind.HUMAN,
            at=self.at,
            kind="system",
        )

    def body(self) -> str:
        """The readable sentence of the verdict, in French (13.1: role OTHER)."""
        if self.kind is VerdictKind.REJECTED:
            parts = [f"Diagnostic de l'agent rejeté par un ingénieur : {sentence(self.rejected_diagnosis)}"]
            if self.reason:
                parts.append(f"Raison : {sentence(self.reason)}")
            if self.correct_diagnosis:
                parts.append(f"Bon diagnostic : {sentence(self.correct_diagnosis)}")
            return " ".join(parts)
        if self.kind is VerdictKind.CORRECTED:
            parts = ["Travail de l'agent corrigé par un ingénieur."]
            if self.reason:
                parts.append(f"Raison : {sentence(self.reason)}")
            if self.correct_diagnosis:
                parts.append(f"Bon diagnostic : {sentence(self.correct_diagnosis)}")
            return " ".join(parts)
        parts = ["Travail de l'agent accepté par un ingénieur."]
        if self.reason:
            parts.append(f"Motif : {sentence(self.reason)}")
        return " ".join(parts)

    def origin(self) -> dict[str, str]:
        """The verdict as catalogue attributes, the reviewer pseudonymised."""
        return {
            "verdict": self.kind.value,
            "verdictPar": pseudonym(self.reviewer),
            "raisonVerdict": self.reason,
            "diagnosticRejete": self.rejected_diagnosis,
            "diagnosticCorrect": self.correct_diagnosis,
        }


def verdict_of(raw: Any) -> HumanVerdict | None:
    """Read the verdict of a raw payload, ``None`` when there is none."""
    if not isinstance(raw, dict):
        return None
    block = raw.get("verdict")
    if not isinstance(block, dict) or not block:
        return None
    try:
        kind = VerdictKind(str(block.get("kind", "")).lower())
    except ValueError:
        return None
    reviewer = str(block.get("reviewer") or block.get("author") or "")
    if not reviewer:
        return None
    return HumanVerdict(
        kind=kind,
        reviewer=reviewer,
        reason=str(block.get("reason") or ""),
        rejected_diagnosis=str(block.get("rejected_diagnosis") or ""),
        correct_diagnosis=str(block.get("correct_diagnosis") or ""),
        at=parse_moment(block.get("at")),
    )


def work_events(events: tuple[TraceEvent, ...]) -> list[TraceEvent]:
    """The elements that are work: the actions, or the tool outputs as a fallback.

    The request that opens the work and the system events - closing, verdict -
    are not work: counting them would make every trace "mixed" and hide who
    really did the job. A trace with neither an action nor a tool output holds
    no work at all, and says so with an empty list rather than a guess.
    """
    actions = [event for event in events if event.kind == "action"]
    if actions:
        return actions
    return [event for event in events if event.kind == "tool_output"]


def author_kind_of(events: tuple[TraceEvent, ...]) -> AuthorKind:
    """Human, agent or mixed: computed from the events, never declared."""
    kinds = {event.author_kind for event in work_events(events)}
    if not kinds:
        return AuthorKind.HUMAN
    if len(kinds) == 1:
        return kinds.pop()
    return AuthorKind.MIXED


def author_shares(events: tuple[TraceEvent, ...]) -> dict[str, int]:
    """The share of each author kind, counted by the code (EF-CAP-09)."""
    shares = {kind.value: 0 for kind in AuthorKind}
    for event in work_events(events):
        shares[event.author_kind.value] += 1
    return shares


def is_verdict_event(event: TraceEvent) -> bool:
    """True when a system event carries the verdict of a human."""
    return event.role is Role.OTHER and any(marker in event.text for marker in VERDICT_MARKERS)


__all__ = [
    "CODE_SOURCES",
    "LABEL_AGENT_CODE",
    "LABEL_AGENT_EXPLOITATION",
    "LABEL_ALARM",
    "LABEL_COMMAND",
    "LABEL_HUMAN",
    "LABEL_SYSTEM",
    "VERDICT_MARKERS",
    "HumanVerdict",
    "VerdictKind",
    "author_kind_of",
    "author_shares",
    "event_text",
    "is_verdict_event",
    "label_for",
    "sentence",
    "strip_label",
    "verdict_of",
    "work_events",
]
