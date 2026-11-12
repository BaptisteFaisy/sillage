"""The stack the tool tests run on: offline, deterministic, in memory.

The demo module builds the same wiring for the demonstration; this one is what
the tests need: a clock that does not move unless a test asks it to, a model
that writes nothing at all, and a registry that keeps the skill records so
that an export can be read back.

Every helper here is a plain function or a plain class, never a fixture, so a
test reads as one line: ``stack = build_stack()`` or ``add_fiche(stack)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from conftest import FIXED_NOW, FixedClock
from sillage.capture import sample
from sillage.capture.ingest import IngestService
from sillage.catalog.local import LocalCatalog
from sillage.kernel.config import Settings
from sillage.kernel.ids import fiche_id as make_fiche_id
from sillage.kernel.protocols import Principal
from sillage.kernel.types import (
    AgentMode,
    Client,
    Fiche,
    Lesson,
    Level,
    ModelTier,
    PrincipalRole,
    Scope,
    ScopeKind,
)
from sillage.memory.local import LocalMemoryBackend
from sillage.memory.namespaces import fiche_namespace, reflection_namespace
from sillage.policy.engine import build_default_engine
from sillage.policy.principal import demo_token, from_token
from sillage.tools.texts import fiche_record, lesson_record

#: The two fictional clients of the demonstration.
CLIENT_A = "a"
CLIENT_B = "b"


class QuietModel:
    """A model that writes nothing: every rule of the code takes over."""

    def __init__(self, answers: dict[str, str] | None = None) -> None:
        self.answers = dict(answers or {})
        self.prompts: list[str] = []

    def complete(
        self,
        *,
        prompt: str,
        system: str = "",
        tier: ModelTier = ModelTier.FAST,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        """The scripted answer of the first matching key, empty when none matches."""
        self.prompts.append(prompt)
        for needle, answer in self.answers.items():
            if needle and needle in prompt:
                return answer
        return ""


@dataclass
class SkillRecord:
    """One record of the registry of the tests."""

    record_id: str
    name: str
    folder: dict[str, str] = field(default_factory=dict)
    state: str = "draft"
    reason: str = ""


@dataclass
class TestRegistry:
    """The registry of the tests: the offline twin of AWS Agent Registry."""

    records: dict[str, SkillRecord] = field(default_factory=dict)

    def register_mcp_server(self, *, name: str, endpoint: str) -> str:
        """Register the MCP server of the gateway."""
        ident = f"mcp-{name}"
        self.records[ident] = SkillRecord(ident, name, {}, "approved")
        return ident

    def register_skill(self, *, name: str, folder: dict[str, str], draft: bool = True) -> str:
        """Register a skill folder, as a draft unless the caller approves it."""
        ident = f"sk-{name}"
        state = "draft" if draft else "approved"
        self.records[ident] = SkillRecord(ident, name, dict(folder), state)
        return ident

    def approve(self, record_id: str) -> None:
        """Submit a record to approval."""
        self.records[record_id].state = "approved"

    def deprecate(self, record_id: str, reason: str = "") -> None:
        """Deprecate a record."""
        self.records[record_id].state = "deprecated"
        self.records[record_id].reason = reason

    def skills(self) -> tuple[SkillRecord, ...]:
        """The skill records only."""
        return tuple(record for record in self.records.values() if record.folder)


@dataclass(frozen=True)
class ToolStack:
    """The ports of Sillage, wired the way the tools expect them."""

    settings: Settings
    clock: Any
    memory: Any
    catalog: Any
    blobs: Any
    journal: Any
    policy: Any
    model: Any
    guardrails: Any
    registry: Any
    notifier: Any
    ingest: IngestService

    def now(self) -> datetime:
        """The instant of the stack: its clock, and nothing else."""
        return self.clock.now()

    def principal(
        self,
        role: PrincipalRole = PrincipalRole.ENGINEER,
        client_id: str | None = None,
        mode: AgentMode | None = None,
    ) -> Principal:
        """A caller built from a demo token, as the gateway builds it."""
        return from_token(demo_token(role, client_id, mode))

    def replace(self, **changes: Any) -> ToolStack:
        """The same stack with one port changed: what a test injects a fake with."""
        from dataclasses import replace as _replace

        return _replace(self, **changes)


def build_stack(*, clock: Any = None, seed: bool = True) -> ToolStack:
    """The offline stack of the tests: no network, no model, no dependency."""
    settings = Settings(offline=True)
    the_clock = clock if clock is not None else FixedClock(FIXED_NOW)
    catalog = LocalCatalog(the_clock)
    memory = LocalMemoryBackend(settings, the_clock)
    model = QuietModel()
    policy = build_default_engine(settings=settings, journal=catalog.journal, clock=the_clock)
    ingest = IngestService(
        settings=settings,
        memory=memory,
        catalog=catalog,
        blobs=catalog.blobs,
        model=model,
        clock=the_clock,
    )
    stack = ToolStack(
        settings=settings,
        clock=the_clock,
        memory=memory,
        catalog=catalog,
        blobs=catalog.blobs,
        journal=catalog.journal,
        policy=policy,
        model=model,
        guardrails=None,
        registry=TestRegistry(),
        notifier=catalog.notifier,
        ingest=ingest,
    )
    if seed:
        seed_stack(stack)
    return stack


def seed_stack(stack: ToolStack) -> dict[str, int]:
    """Two clients and one closed ticket: the smallest history worth searching."""
    stack.catalog.put_client(
        Client(client_id=CLIENT_A, name="Client A - retail", technologies=("postgresql", "ecs"))
    )
    stack.catalog.put_client(
        Client(client_id=CLIENT_B, name="Client B - logistics", technologies=("mysql", "eks"))
    )
    report = stack.ingest.ingest_trace(sample.ticket_2041())
    return {"clients": 2, "episodes": 1 if report.events_created else 0}


#: The default content of a fiche of the tests: one validated fiche of client A.
FICHE_DEFAULTS: dict[str, Any] = {
    "title": "Connexions à la base épuisées après un déploiement",
    "body": "Le groupe de connexions a été multiplié par cinq lors du dernier déploiement.",
    "steps": (
        "Comparer la taille du groupe de connexions avant et après le dernier déploiement",
        "Multiplier cette taille par le nombre d'instances et comparer au maximum de la base",
    ),
    "avoid": ("Redémarrer le service : les connexions reviennent au maximum en quelques minutes",),
    "validity": "Application avec groupe de connexions par instance, base relationnelle gérée",
    "claims": (),
    "sources": ("guichet:ticket-2041",),
    "provenance": "2 épisodes d'ingénieurs, 1 épisode d'agent corrigé par un ingénieur",
    "success_count": 0,
    "failure_count": 0,
    "consecutive_failures": 0,
    "distinct_authors": (),
    "last_confirmation": None,
    "technologies": ("postgresql", "ecs"),
}


def add_fiche(
    stack: ToolStack,
    *,
    scope: Scope | None = None,
    level: Level = Level.VALIDATED,
    **changes: Any,
) -> Fiche:
    """A fiche, written in the catalogue and in Memory, as the tools do."""
    perimeter = scope if scope is not None else Scope(kind=ScopeKind.CLIENT, client_id=CLIENT_A)
    content = {**FICHE_DEFAULTS, **changes}
    ident = make_fiche_id(perimeter.actor_id, str(content["title"]))
    fiche = Fiche(
        fiche_id=ident,
        scope=perimeter,
        title=str(content["title"]),
        body=str(content["body"]),
        steps=tuple(content["steps"]),
        avoid=tuple(content["avoid"]),
        validity=str(content["validity"]),
        claims=tuple(content["claims"]),
        sources=tuple(content["sources"]),
        level=level,
        validated_by="p-referent",
        validated_at=FIXED_NOW,
        success_count=int(content["success_count"]),
        failure_count=int(content["failure_count"]),
        consecutive_failures=int(content["consecutive_failures"]),
        distinct_authors=tuple(content["distinct_authors"]),
        last_confirmation=content["last_confirmation"],
        provenance=str(content["provenance"]),
        technologies=tuple(content["technologies"]),
        memory_record_id=ident,
    )
    record = fiche_record(fiche, namespace=fiche_namespace(stack.settings, perimeter), created_at=FIXED_NOW)
    stack.memory.put_records([record])
    stack.catalog.put_fiche(fiche)
    return fiche


def add_lesson(
    stack: ToolStack,
    *,
    scope: Scope | None = None,
    use_case: str = "Rétablir un service qui épuise les connexions à sa base",
    advice: tuple[str, ...] = ("Comparer le groupe de connexions avant la taille de la base",),
    pitfalls: tuple[str, ...] = ("Base saturée après un déploiement : vérifier les connexions",),
    episode_ids: tuple[str, ...] = (),
    level: Level = Level.PROPOSED,
    confidence: float = 0.7,
) -> Lesson:
    """A lesson, written in the catalogue and in Memory."""
    perimeter = scope if scope is not None else Scope(kind=ScopeKind.CLIENT, client_id=CLIENT_A)
    ident = f"le-test-{len(tuple(stack.catalog.list_lessons())) + 1}"
    lesson = Lesson(
        lesson_id=ident,
        scope=perimeter,
        use_case=use_case,
        advice=advice,
        pitfalls=pitfalls,
        confidence=confidence,
        episode_ids=episode_ids,
        level=level,
        created_at=FIXED_NOW,
        memory_record_id=ident,
    )
    record = lesson_record(
        lesson, namespace=reflection_namespace(stack.settings, perimeter), created_at=FIXED_NOW
    )
    stack.memory.put_records([record])
    stack.catalog.put_lesson(lesson)
    return lesson


__all__ = [
    "CLIENT_A",
    "CLIENT_B",
    "FIXED_NOW",
    "QuietModel",
    "SkillRecord",
    "TestRegistry",
    "ToolStack",
    "add_fiche",
    "add_lesson",
    "build_stack",
    "seed_stack",
]
