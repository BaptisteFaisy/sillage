"""Core value objects of Sillage.

Frozen dataclasses and enums only: no I/O, no AWS call, no model call. Every
other module speaks this vocabulary, which is what keeps the offline test
suite and the deployed stack interchangeable (see ``docs/CONTRACT.md``).

French domain words are kept on purpose (``Fiche``, ``Lecon``, ``Périmètre``
spelled ``scope``): they are the vocabulary of the specification and of the
demo portal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class ScopeKind(StrEnum):
    """The unit of isolation. A client, the MSP itself, or everybody."""

    CLIENT = "client"
    INTERNAL = "internal"
    COMMON = "common"


class Role(StrEnum):
    """Conversational roles accepted by AgentCore Memory events."""

    USER = "USER"
    ASSISTANT = "ASSISTANT"
    TOOL = "TOOL"
    OTHER = "OTHER"


class AuthorKind(StrEnum):
    """Who did the work: a human, an agent, or one started and the other finished."""

    HUMAN = "human"
    AGENT = "agent"
    MIXED = "mixed"


class AgentMode(StrEnum):
    """An agent working without human review, or with a human reviewing it."""

    AUTONOMOUS = "autonomous"
    ASSISTED = "assisted"


class PrincipalRole(StrEnum):
    """Roles carried by a token. Never inferred from free text."""

    ENGINEER = "engineer"
    REFERENT = "referent"
    AGENT = "agent"
    TEAM_LEAD = "team_lead"
    ADMIN = "admin"


class Source(StrEnum):
    """Where a finished job comes from."""

    GUICHET = "guichet"
    DEVOPS_AGENT = "devops_agent"
    KIRO = "kiro"
    MERGE = "merge"
    SELF_DECLARED = "self_declared"
    PORTAL = "portal"


class Outcome(StrEnum):
    """Did the work succeed? Failures are first-class citizens."""

    SUCCESS = "success"
    FAILURE = "failure"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class Level(StrEnum):
    """Trust level of a knowledge object (specification chapter 10.2)."""

    RAW = "raw"  # 0 - episode straight out of the distillation
    PROPOSED = "proposed"  # 1 - lesson produced by reflection
    VALIDATED = "validated"  # 2 - fiche reviewed by a referent
    PROVEN = "proven"  # 3 - 3 successes by at least 2 distinct authors
    SET_ASIDE = "set_aside"  # E - failed twice in a row, no longer served to agents
    WITHDRAWN = "withdrawn"  # R - a referent removed it


#: Display code of each level, as used by the portal and the MCP answers.
LEVEL_CODE: dict[Level, str] = {
    Level.RAW: "0",
    Level.PROPOSED: "1",
    Level.VALIDATED: "2",
    Level.PROVEN: "3",
    Level.SET_ASIDE: "E",
    Level.WITHDRAWN: "R",
}

#: Levels an autonomous agent is allowed to read (specification 10.2).
SERVED_TO_AUTONOMOUS_AGENT: frozenset[Level] = frozenset({Level.VALIDATED, Level.PROVEN})

#: Levels a human is allowed to read, with the proper warning.
SERVED_TO_HUMAN: frozenset[Level] = frozenset(
    {Level.RAW, Level.PROPOSED, Level.VALIDATED, Level.PROVEN, Level.SET_ASIDE}
)


class FeedbackResult(StrEnum):
    """What a consumer says after using a fiche."""

    HELPED = "helped"
    DID_NOT_HELP = "did_not_help"
    WRONG = "wrong"


class ModelTier(StrEnum):
    """A fast model to normalise and rewrite, a capable one to draft."""

    FAST = "fast"
    CAPABLE = "capable"


class ExtractionMode(StrEnum):
    """``SKIP`` stores a trace without distilling it ("do not keep")."""

    DEFAULT = "DEFAULT"
    SKIP = "SKIP"


class Decision(StrEnum):
    """Verdict of the policy engine, logged either way."""

    PERMIT = "permit"
    FORBID = "forbid"
    DEFAULT_DENY = "default_deny"


@dataclass(frozen=True)
class Scope:
    """A perimeter: one client, the MSP, or the shared common knowledge.

    ``actor_id`` is the AgentCore Memory actor. The design choice is that **the
    actor is the perimeter, not the person** (specification 12.5): reflection
    then crosses every job of one client and never two clients.
    """

    kind: ScopeKind
    client_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind is ScopeKind.CLIENT and not self.client_id:
            raise ValueError("a client scope needs a client_id")
        if self.kind is not ScopeKind.CLIENT and self.client_id:
            raise ValueError(f"scope {self.kind.value} carries no client_id")

    @property
    def actor_id(self) -> str:
        if self.kind is ScopeKind.CLIENT:
            return f"client-{self.client_id}"
        return "interne" if self.kind is ScopeKind.INTERNAL else "commun"

    @property
    def label(self) -> str:
        return self.actor_id

    @property
    def is_common(self) -> bool:
        return self.kind is ScopeKind.COMMON


@dataclass(frozen=True)
class TraceEvent:
    """One dated element of a normalised trace (specification 13.1)."""

    role: Role
    text: str
    author: str | None = None
    author_kind: AuthorKind = AuthorKind.HUMAN
    at: datetime | None = None
    kind: str = "action"  # request | action | tool_output | system


@dataclass(frozen=True)
class Turn:
    """One step of an episode: what was intended, done and obtained."""

    action: str
    reasoning: str = ""
    result: str = ""


@dataclass(frozen=True)
class Trace:
    """A finished job put in the common format, before distillation."""

    work_item_id: str
    scope: Scope
    source: Source
    author_kind: AuthorKind
    author: str  # pseudonym, never a real name (specification 17.1)
    events: tuple[TraceEvent, ...] = ()
    technologies: tuple[str, ...] = ()
    closed_at: datetime | None = None
    do_not_distill: bool = False
    origin: dict[str, str] = field(default_factory=dict)
    received_at: datetime | None = None

    @property
    def session_id(self) -> str:
        """Memory session id: the identity of the work itself."""
        return self.work_item_id

    @property
    def metadata(self) -> dict[str, str]:
        """Event metadata, at most 15 keys (AgentCore Memory limit)."""
        return {
            "source": self.source.value,
            "auteurType": self.author_kind.value,
            "auteur": self.author,
            "techno": ",".join(self.technologies[:4]),
            "scope": self.scope.actor_id,
            "workItemId": self.work_item_id,
            "doNotDistill": "1" if self.do_not_distill else "0",
        }


@dataclass(frozen=True)
class Episode:
    """The structured summary of one finished job, produced by Memory."""

    episode_id: str
    scope: Scope
    work_item_id: str
    situation: str = ""
    intent: str = ""
    turns: tuple[Turn, ...] = ()
    outcome: Outcome = Outcome.UNKNOWN
    justification: str = ""
    teachings: tuple[str, ...] = ()
    failed_paths: tuple[str, ...] = ()
    memory_record_id: str | None = None
    source: Source = Source.GUICHET
    author_kind: AuthorKind = AuthorKind.HUMAN
    author: str = ""
    technologies: tuple[str, ...] = ()
    level: Level = Level.RAW
    created_at: datetime | None = None
    rejected_diagnosis: str = ""
    corrected_by: str = ""


@dataclass(frozen=True)
class Lesson:
    """What several close episodes teach: a "reflection" in AgentCore terms."""

    lesson_id: str
    scope: Scope
    use_case: str = ""
    advice: tuple[str, ...] = ()
    pitfalls: tuple[str, ...] = ()
    confidence: float = 0.5  # 0.1 .. 1.0, provided by AgentCore Memory
    episode_ids: tuple[str, ...] = ()
    memory_record_id: str | None = None
    level: Level = Level.PROPOSED
    created_at: datetime | None = None


@dataclass(frozen=True)
class Claim:
    """One statement of a fiche. It must cite at least one episode."""

    text: str
    episode_ids: tuple[str, ...] = ()

    @property
    def sourced(self) -> bool:
        return bool(self.episode_ids)


@dataclass(frozen=True)
class Fiche:
    """A lesson reviewed, corrected and validated by a human referent."""

    fiche_id: str
    scope: Scope
    version: int = 1
    title: str = ""
    body: str = ""
    steps: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    validity: str = ""
    claims: tuple[Claim, ...] = ()
    sources: tuple[str, ...] = ()  # work item ids of the origin episodes
    level: Level = Level.PROPOSED
    validated_by: str = ""
    validated_at: datetime | None = None
    memory_record_id: str | None = None
    success_count: int = 0
    failure_count: int = 0
    no_feedback_count: int = 0
    consecutive_failures: int = 0
    distinct_authors: tuple[str, ...] = ()
    last_confirmation: datetime | None = None
    provenance: str = ""
    technologies: tuple[str, ...] = ()
    set_aside_reason: str = ""
    lesson_id: str = ""


@dataclass(frozen=True)
class Usage:
    """One consultation. It gets an id so the feedback can be attached."""

    usage_id: str
    served_ids: tuple[str, ...] = ()
    caller_role: PrincipalRole = PrincipalRole.ENGINEER
    caller_kind: AuthorKind = AuthorKind.HUMAN
    caller_mode: AgentMode | None = None
    scope: Scope | None = None
    at: datetime | None = None
    problem: str = ""


@dataclass(frozen=True)
class Feedback:
    """What the consumer said after using the served knowledge."""

    usage_id: str
    result: FeedbackResult
    note: str = ""
    at: datetime | None = None
    fiche_id: str = ""
    caller: str = ""


@dataclass(frozen=True)
class Client:
    """A tenant of the MSP. Sharing is a contract option, never a default."""

    client_id: str
    name: str
    no_common: bool = False
    retention_days: dict[str, int] = field(default_factory=dict)
    technologies: tuple[str, ...] = ()


@dataclass(frozen=True)
class JournalEntry:
    """One tool call or one policy decision. Append-only (ENF-SEC-05)."""

    entry_id: str
    at: datetime
    caller: str
    caller_role: PrincipalRole
    tool: str
    decision: Decision
    reason: str = ""
    scope: str = ""
    usage_id: str = ""
    work_item_id: str = ""


@dataclass(frozen=True)
class ServedFiche:
    """A fiche as served by ``recall``: content plus everything that backs it."""

    fiche: Fiche
    success_rate: float
    freshness_days: int | None
    generalisation_confidence: float | None = None

    @property
    def level_code(self) -> str:
        return LEVEL_CODE[self.fiche.level]


@dataclass(frozen=True)
class RecallResult:
    """Compact answer of ``recall``: at most 3 fiches, 2 episodes, the traps."""

    usage_id: str
    fiches: tuple[ServedFiche, ...] = ()
    common: tuple[ServedFiche, ...] = ()
    episodes: tuple[Episode, ...] = ()
    lessons: tuple[Lesson, ...] = ()
    avoid: tuple[str, ...] = ()
    not_served_note: str = ""
    estimated_tokens: int = 0
    intent: str = ""
    scope: Scope | None = None
