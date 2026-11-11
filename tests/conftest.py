"""Shared fixtures: an offline stack, a fixed clock, demo clients.

Every test runs offline. Nothing here touches the network, boto3 or a model:
the fakes below are the same ports the AWS implementations satisfy.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:  # usable without an editable install
    sys.path.insert(0, str(SRC))

from sillage.kernel.config import Settings  # noqa: E402
from sillage.kernel.protocols import Principal  # noqa: E402
from sillage.kernel.types import (  # noqa: E402
    AgentMode,
    AuthorKind,
    Client,
    PrincipalRole,
    Role,
    Scope,
    ScopeKind,
    Source,
    Trace,
    TraceEvent,
)

FIXED_NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


class FixedClock:
    """A clock that only moves when a test asks it to."""

    def __init__(self, now: datetime = FIXED_NOW) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance_days(self, days: int) -> datetime:
        self._now = self._now + __import__("datetime").timedelta(days=days)
        return self._now


@pytest.fixture
def settings() -> Settings:
    return Settings(offline=True)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def scope_a() -> Scope:
    return Scope(kind=ScopeKind.CLIENT, client_id="a")


@pytest.fixture
def scope_b() -> Scope:
    return Scope(kind=ScopeKind.CLIENT, client_id="b")


@pytest.fixture
def clients() -> tuple[Client, Client]:
    return (
        Client(client_id="a", name="Client A - retail", technologies=("postgresql", "ecs")),
        Client(client_id="b", name="Client B - logistics", no_common=False, technologies=("mysql", "eks")),
    )


@pytest.fixture
def referent() -> Principal:
    return Principal(subject="p-referent", role=PrincipalRole.REFERENT, client_id="a")


@pytest.fixture
def engineer() -> Principal:
    return Principal(subject="p-engineer", role=PrincipalRole.ENGINEER, client_id="a")


@pytest.fixture
def autonomous_agent_a() -> Principal:
    return Principal(
        subject="agent-devops-a", role=PrincipalRole.AGENT, client_id="a", mode=AgentMode.AUTONOMOUS
    )


@pytest.fixture
def assisted_agent_b() -> Principal:
    return Principal(subject="agent-kiro-b", role=PrincipalRole.AGENT, client_id="b", mode=AgentMode.ASSISTED)


@pytest.fixture
def admin() -> Principal:
    return Principal(subject="p-admin", role=PrincipalRole.ADMIN)


def make_event(
    role: Role, text: str, kind: str = "action", author_kind: AuthorKind = AuthorKind.HUMAN
) -> TraceEvent:
    return TraceEvent(role=role, text=text, kind=kind, author_kind=author_kind, at=FIXED_NOW)


@pytest.fixture
def trace_ticket() -> Trace:
    """The annex B ticket: a wrong lead, then the real cause."""
    return Trace(
        work_item_id="guichet:ticket-2041",
        scope=Scope(kind=ScopeKind.CLIENT, client_id="a"),
        source=Source.GUICHET,
        author_kind=AuthorKind.HUMAN,
        author="p-7f3a",
        technologies=("postgresql", "ecs"),
        closed_at=FIXED_NOW,
        events=(
            make_event(
                Role.USER,
                "[alarme] Le site renvoie des erreurs 500 depuis 14h02. Journaux : too many connections.",
                kind="request",
            ),
            make_event(
                Role.ASSISTANT, "[ingénieur] Hypothèse : base sous-dimensionnée. Je redémarre le service."
            ),
            make_event(
                Role.TOOL,
                "[commande] Redémarrage effectué. Connexions ouvertes : 12, puis 100 au bout de 4 minutes.",
                kind="tool_output",
            ),
            make_event(
                Role.ASSISTANT,
                "[ingénieur] Le redémarrage ne règle rien. Le déploiement de 13h50 a passé le pool "
                "de 10 à 50 par instance. Je reviens à 10.",
            ),
            make_event(
                Role.TOOL,
                "[commande] Configuration rétablie. Connexions stables à 38. Plus d'erreur 500.",
                kind="tool_output",
            ),
            make_event(
                Role.OTHER, "[système] Ticket clos. Résolution confirmée par le demandeur.", kind="system"
            ),
        ),
        origin={"ticket": "2041", "code": "INC-500"},
    )
