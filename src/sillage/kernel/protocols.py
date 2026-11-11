"""Ports of Sillage: the interfaces every backend implements twice.

Each port has an AWS implementation (boto3 / AgentCore / Bedrock) and an
offline implementation used by the test suite, the demo and the replay bench.
Nothing above this file imports boto3, which is what keeps ``make test``
hermetic and inside the 5 minute CI budget.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from sillage.kernel.types import (
    AgentMode,
    Client,
    Decision,
    Episode,
    ExtractionMode,
    Feedback,
    Fiche,
    JournalEntry,
    Lesson,
    Level,
    ModelTier,
    PrincipalRole,
    Scope,
    ScopeKind,
    Trace,
    Usage,
)


@runtime_checkable
class Clock(Protocol):
    """Single source of truth for "now": freshness is computed, not guessed."""

    def now(self) -> datetime: ...


@dataclass(frozen=True)
class MemoryRecord:
    """One record read from (or written to) AgentCore Memory."""

    record_id: str
    namespace: str
    content: str
    score: float = 0.0
    metadata: dict[str, str] = field(default_factory=dict)
    created_at: datetime | None = None


@runtime_checkable
class MemoryBackend(Protocol):
    """AgentCore Memory with an episodic strategy (specification 12.2, 12.5)."""

    def ensure_memory(self, name: str, description: str) -> str:
        """Create or reuse the memory resource; return its id."""

    def create_events(
        self,
        *,
        actor_id: str,
        session_id: str,
        trace: Trace,
        extraction_mode: ExtractionMode = ExtractionMode.DEFAULT,
    ) -> str:
        """Replay a normalised trace as dated events (100 per call maximum)."""

    def retrieve(self, *, namespace: str, query: str, top_k: int = 5) -> Sequence[MemoryRecord]:
        """Semantic search inside one namespace."""

    def list_records(self, *, namespace_path: str) -> Sequence[MemoryRecord]:
        """Walk every record under a namespace prefix."""

    def put_records(self, records: Sequence[MemoryRecord]) -> None:
        """Write approved text verbatim (``BatchCreateMemoryRecords``)."""

    def delete_records(self, *, actor_id: str, session_id: str) -> int:
        """Forget one job; return how many records disappeared."""

    def reflections(self, *, actor_id: str) -> Sequence[Lesson]:
        """Lessons produced by the service for one perimeter."""


@runtime_checkable
class ModelBackend(Protocol):
    """Amazon Bedrock models, behind one call shape."""

    def complete(
        self,
        *,
        prompt: str,
        system: str = "",
        tier: ModelTier = ModelTier.FAST,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        """Return the model answer as text (JSON when a schema is given)."""


@runtime_checkable
class GuardrailFilter(Protocol):
    """Bedrock Guardrails: sensitive data and attack detection."""

    def scan(self, text: str) -> GuardrailVerdict: ...


@dataclass(frozen=True)
class GuardrailVerdict:
    """Result of a guardrail scan. Blocked content never reaches Memory."""

    blocked: bool = False
    action: Literal["none", "masked", "blocked"] = "none"
    masked_text: str = ""
    reasons: tuple[str, ...] = ()


@runtime_checkable
class CatalogStore(Protocol):
    """The DynamoDB catalogue: exact state, computed by code (ENF-IA-01)."""

    # -- clients
    def put_client(self, client: Client) -> None: ...
    def get_client(self, client_id: str) -> Client | None: ...
    def list_clients(self) -> Sequence[Client]: ...

    # -- traces and episodes
    def put_trace(self, trace: Trace) -> None: ...
    def get_trace(self, work_item_id: str) -> Trace | None: ...
    def trace_seen(self, fingerprint: str) -> bool: ...
    def mark_trace_seen(self, fingerprint: str, work_item_id: str) -> None: ...
    def put_episode(self, episode: Episode) -> None: ...
    def get_episode(self, episode_id: str) -> Episode | None: ...
    def list_episodes(self, *, scope: Scope | None = None, limit: int = 50) -> Sequence[Episode]: ...

    # -- lessons and fiches
    def put_lesson(self, lesson: Lesson) -> None: ...
    def get_lesson(self, lesson_id: str) -> Lesson | None: ...
    def list_lessons(self, *, scope: Scope | None = None) -> Sequence[Lesson]: ...
    def put_fiche(self, fiche: Fiche) -> None: ...
    def get_fiche(self, fiche_id: str, version: int | None = None) -> Fiche | None: ...
    def list_fiches(self, *, scope: Scope | None = None, level: Level | None = None) -> Sequence[Fiche]: ...
    def search_fiches(self, *, scope: Scope, terms: Sequence[str], limit: int = 10) -> Sequence[Fiche]: ...

    # -- usage and feedback
    def put_usage(self, usage: Usage) -> None: ...
    def get_usage(self, usage_id: str) -> Usage | None: ...
    def put_feedback(self, feedback: Feedback) -> None: ...
    def list_feedback(self, fiche_id: str) -> Sequence[Feedback]: ...

    # -- deletion cascade
    def delete_scope(self, scope: Scope) -> dict[str, int]: ...


@runtime_checkable
class BlobStore(Protocol):
    """Versioned copy of every normalised trace (S3 in AWS, disk offline)."""

    def put(self, key: str, payload: str) -> str: ...
    def get(self, key: str) -> str | None: ...
    def delete(self, key: str) -> int: ...


@dataclass(frozen=True)
class Principal:
    """Who is calling. Built from token claims only, never from the payload."""

    subject: str
    role: PrincipalRole
    client_id: str | None = None
    mode: AgentMode | None = None
    scopes: tuple[str, ...] = ()

    @property
    def is_agent(self) -> bool:
        return self.role is PrincipalRole.AGENT

    @property
    def is_human(self) -> bool:
        return not self.is_agent

    @property
    def autonomous(self) -> bool:
        return self.is_agent and self.mode is AgentMode.AUTONOMOUS

    def home_scope(self) -> Scope | None:
        """The perimeter this principal is confined to, if any."""
        if self.client_id:
            return Scope(kind=ScopeKind.CLIENT, client_id=self.client_id)
        return None

    def allowed_scopes(self) -> tuple[Scope, ...]:
        """Home perimeter plus the common knowledge, and nothing else."""
        home = self.home_scope()
        common = Scope(kind=ScopeKind.COMMON)
        if home is None:
            return (Scope(kind=ScopeKind.INTERNAL), common)
        return (home, common)


@dataclass(frozen=True)
class ToolRequest:
    """One tool call as the gateway sees it."""

    tool: str
    principal: Principal
    arguments: dict[str, Any] = field(default_factory=dict)

    @property
    def client_id(self) -> str | None:
        value = self.arguments.get("clientId")
        return value if isinstance(value, str) else None


@dataclass(frozen=True)
class Authorization:
    """The decision, and why. Refusals are visible in the journal."""

    decision: Decision
    reason: str = ""
    rule: str = ""


@runtime_checkable
class PolicyEngine(Protocol):
    """Layer 1: decided outside the agents' code (specification 10.1)."""

    mode: str  # "LOG_ONLY" | "ENFORCE"

    def authorize(self, request: ToolRequest) -> Authorization: ...


@runtime_checkable
class Journal(Protocol):
    """Append-only log of tool calls and policy decisions."""

    def append(self, entry: JournalEntry) -> None: ...
    def query(
        self, *, limit: int = 50, tool: str = "", decision: Decision | None = None
    ) -> Sequence[JournalEntry]: ...


@runtime_checkable
class Registry(Protocol):
    """AWS Agent Registry: MCP server and skill records, with approval."""

    def register_mcp_server(self, *, name: str, endpoint: str) -> str: ...
    def register_skill(self, *, name: str, folder: dict[str, str], draft: bool = True) -> str: ...
    def approve(self, record_id: str) -> None: ...
    def deprecate(self, record_id: str, reason: str = "") -> None: ...


@runtime_checkable
class Notifier(Protocol):
    """Tell a referent that a fiche needs a decision."""

    def notify(self, *, referent: str, subject: str, body: str, fiche_id: str = "") -> None: ...
