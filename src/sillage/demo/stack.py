"""The stack: one call wires every port, offline by default.

``build()`` is the single place where the twelve ports of Sillage are assembled.
The smoke, the tests, the CLI, the MCP tools, the replay bench, the portal API and
the public try page all receive the same object, which is what stops each of them
from inventing its own wiring (CONTRACT.md §11).

Two invariants are absolute:

* **zero dependency** - the offline stack imports neither boto3, nor fastapi, nor
  strands, and calls neither the network nor a model. The AWS implementations are
  reached by :mod:`sillage.demo.wiring` through a lazy import, and a missing boto3
  raises :class:`BackendUnavailable` instead of breaking the offline mode;
* **under two seconds** - ``build()`` seeds a whole demo history in memory, so a
  test that builds a stack stays inside the five minute CI budget.

Every port can be replaced by a keyword: that is how the tests inject a fake and
how the replay bench freezes a clock. A key given in ``overrides`` wins even when
its value is ``None`` - the guardrails port is optional by contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sillage.capture.ingest import IngestService
from sillage.demo.history import demo_clients
from sillage.demo.wiring import aws_parts, chosen, offline_parts
from sillage.kernel.config import Settings
from sillage.kernel.protocols import (
    Authorization,
    BlobStore,
    CatalogStore,
    Clock,
    GuardrailFilter,
    Journal,
    MemoryBackend,
    ModelBackend,
    Notifier,
    Principal,
    Registry,
    ToolRequest,
)
from sillage.kernel.types import AgentMode, Client, Decision, PrincipalRole, Scope, ScopeKind
from sillage.memory.search import SearchHit
from sillage.memory.search import search as search_namespaces
from sillage.policy.engine import PolicyEngine, build_default_engine
from sillage.policy.principal import demo_token, from_token

#: The two fictional clients of the demonstration (specification 19.1).
DEFAULT_CLIENTS: tuple[str, ...] = ("a", "b")


@dataclass(frozen=True)
class Stack:
    """The twelve ports of Sillage, wired once and shared by everybody.

    The fields are exactly the ones of CONTRACT.md §11, in the same order: the
    tools, the replay bench and the portal API are written against them. The
    methods below add no state - they are the three questions every caller asks
    (which perimeter, which caller, may it) plus the two reads the offline ports
    allow (the journal and the notifications).
    """

    settings: Settings
    clock: Clock
    memory: MemoryBackend
    catalog: CatalogStore
    blobs: BlobStore
    journal: Journal
    policy: PolicyEngine
    model: ModelBackend
    guardrails: GuardrailFilter | None
    registry: Registry
    notifier: Notifier
    ingest: IngestService

    # -- perimeters and callers --------------------------------------------- #
    def clients(self) -> tuple[Client, ...]:
        """The tenants registered in the catalogue, sorted by identifier."""
        return tuple(self.catalog.list_clients())

    def scope(self, client_id: str | None = None) -> Scope:
        """The perimeter of a client, or the internal one of the MSP."""
        if client_id:
            return Scope(kind=ScopeKind.CLIENT, client_id=client_id)
        return Scope(kind=ScopeKind.INTERNAL)

    def perimeters(self) -> tuple[Scope, ...]:
        """Every perimeter of the demo: one per client, then the internal one."""
        return (*[self.scope(client.client_id) for client in self.clients()], self.scope())

    def principal(
        self,
        role: PrincipalRole | str = PrincipalRole.ENGINEER,
        client_id: str | None = None,
        mode: AgentMode | str | None = None,
    ) -> Principal:
        """A demo caller, built from a demo token and from nothing else.

        The token is the one :func:`sillage.policy.principal.demo_token` writes,
        so the demo, the public try page and the tests all get a principal the
        exact same way the gateway does (specification 10.4, 12.6).
        """
        return from_token(demo_token(_role(role), client_id, _mode(mode)))

    # -- the policy, before any tool ---------------------------------------- #
    def request(self, tool: str, principal: Principal, **arguments: Any) -> ToolRequest:
        """One call as the gateway sees it."""
        return ToolRequest(tool=tool, principal=principal, arguments=dict(arguments))

    def decide(self, tool: str, principal: Principal, **arguments: Any) -> Authorization:
        """The decision on a call, journalised whatever it is (ENF-SEC-05)."""
        return self.policy.authorize(self.request(tool, principal, **arguments))

    def permitted(self, tool: str, principal: Principal, **arguments: Any) -> bool:
        """True when layer 1 lets the call through."""
        return self.decide(tool, principal, **arguments).decision is Decision.PERMIT

    def enforce(self, tool: str, principal: Principal, **arguments: Any) -> None:
        """Decide and refuse by raising, the way the gateway does (CONTRACT §5).

        The decision is journalised exactly once: this is
        :meth:`PolicyEngine.enforce`, not a second call to :meth:`decide`.
        """
        self.policy.enforce(self.request(tool, principal, **arguments))

    # -- the memory ---------------------------------------------------------- #
    def search(
        self, *, scope: Scope | None = None, client_id: str | None = None, query: str = "", top_k: int = 0
    ) -> tuple[SearchHit, ...]:
        """What one perimeter may read about a problem, best first (13.4)."""
        perimeter = scope if scope is not None else self.scope(client_id)
        limit = top_k or self.settings.recall_top_k
        return search_namespaces(
            self.settings,
            self.memory,
            self.catalog,
            scope=perimeter,
            query=query,
            top_k=limit,
            model=self.model,
            now=self.now(),
        )

    # -- reads the offline ports allow --------------------------------------- #
    def now(self) -> datetime:
        """The single source of truth for "now": freshness is computed, not guessed."""
        return self.clock.now()

    def entries(self, **filters: Any) -> tuple[Any, ...]:
        """The journal of the decisions, newest first, filtered as ``Journal.query``."""
        return tuple(self.journal.query(**filters))

    def notifications(self) -> tuple[Any, ...]:
        """What the referents received, oldest first; empty for a notifier that keeps nothing."""
        reader = getattr(self.notifier, "messages", None)
        return tuple(reader()) if callable(reader) else ()

    def replace(self, **changes: Any) -> Stack:
        """The same stack with one port changed: what a test injects a fake with."""
        return replace(self, **changes)


# -- the wiring -------------------------------------------------------------- #
def build(
    *,
    seed: bool = True,
    settings: Settings | None = None,
    clients: Sequence[str] = DEFAULT_CLIENTS,
    offline: bool | None = None,
    **overrides: Any,
) -> Stack:
    """Assemble a stack, register its perimeters and - by default - seed the demo.

    ``offline`` defaults to ``settings.offline`` (``True`` out of the box, so the
    smoke and the tests never reach AWS). ``overrides`` replaces any port by name:
    ``clock``, ``memory``, ``catalog``, ``blobs``, ``journal``, ``notifier``,
    ``policy``, ``model``, ``guardrails``, ``registry``, ``ingest``. In AWS mode a
    missing boto3 raises :class:`BackendUnavailable`, and the offline mode is
    untouched.
    """
    resolved = _settings(settings, offline)
    parts = _parts(resolved, overrides)
    stack = assemble(parts, overrides)
    register_clients(stack, clients)
    if seed:
        from sillage.demo.seed import seed_stack  # local import: seed reads the stack

        seed_stack(stack)
    return stack


def assemble(parts: dict[str, Any], overrides: dict[str, Any]) -> Stack:
    """Build the stack out of the ports the wiring produced, policy and capture included."""
    settings: Settings = parts["settings"]
    clock: Clock = parts["clock"]
    policy = chosen(
        overrides,
        "policy",
        lambda: build_default_engine(settings=settings, journal=parts["journal"], clock=clock),
    )
    ingest = chosen(
        overrides,
        "ingest",
        lambda: IngestService(
            settings=settings,
            memory=parts["memory"],
            catalog=parts["catalog"],
            blobs=parts["blobs"],
            model=parts["model"],
            guardrails=parts["guardrails"],
            clock=clock,
        ),
    )
    return Stack(
        settings=settings,
        clock=clock,
        memory=parts["memory"],
        catalog=parts["catalog"],
        blobs=parts["blobs"],
        journal=parts["journal"],
        policy=policy,
        model=parts["model"],
        guardrails=parts["guardrails"],
        registry=parts["registry"],
        notifier=parts["notifier"],
        ingest=ingest,
    )


def register_clients(stack: Stack, clients: Sequence[str] = DEFAULT_CLIENTS) -> tuple[Client, ...]:
    """Write the tenant rows of the demo: two fictional clients (specification 19.1)."""
    written = demo_clients(clients)
    for client in written:
        stack.catalog.put_client(client)
    return written


def _settings(settings: Settings | None, offline: bool | None) -> Settings:
    """The settings to wire with: the given ones, else the environment."""
    resolved = settings if settings is not None else Settings.from_env()
    if offline is None or resolved.offline == offline:
        return resolved
    return replace(resolved, offline=offline)


def _parts(settings: Settings, overrides: dict[str, Any]) -> dict[str, Any]:
    """The ten ports of one mode; the AWS half is the one that imports boto3."""
    if settings.offline:
        return offline_parts(settings=settings, overrides=overrides)
    return aws_parts(settings=settings, overrides=overrides)


def _role(role: PrincipalRole | str) -> PrincipalRole:
    """A role given as its value or as the enum."""
    return role if isinstance(role, PrincipalRole) else PrincipalRole(str(role).strip().lower())


def _mode(mode: AgentMode | str | None) -> AgentMode | None:
    """A mode given as its value, as the enum, or not given at all."""
    if mode is None or isinstance(mode, AgentMode):
        return mode
    folded = str(mode).strip().lower()
    return AgentMode.AUTONOMOUS if folded in {"autonome", "autonomous"} else AgentMode(folded)


__all__ = ["DEFAULT_CLIENTS", "Stack", "assemble", "build", "register_clients"]
