"""The ports no merged module provides yet: a model and a registry.

Two kernel ports have no implementation anywhere in the repository when the demo
wiring is written:

* ``ModelBackend`` - :mod:`sillage.agents.models` will hold ``BedrockModels`` and
  the real ``OfflineModels``;
* ``Registry`` - AWS Agent Registry has no offline twin yet.

Both are written here, in the ``demo`` package, and both are **provisional**: the
day the ``agents`` lot lands, :func:`sillage.demo.stack.build` picks its model up
and this file keeps only the registry. Nothing outside ``demo`` may import them
as if they were the real thing.

Two rules shape them:

* no duplication - the offline model deliberately answers **nothing** for the
  prompts whose deterministic rule already exists elsewhere (the intent
  reformulation of :mod:`sillage.memory.search`, the journal summary of
  :mod:`sillage.capture.logs`, the distillation of :mod:`sillage.memory.distill`).
  An empty answer makes every one of those callers fall back on its own rule, so
  one logic stays one implementation;
* no number written by a model (ENF-IA-01) - the offline model writes no text at
  all by default, and the registry stores no metric.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from sillage.kernel.errors import NotFound
from sillage.kernel.ids import make_id
from sillage.kernel.protocols import Clock, ModelBackend, Registry  # noqa: F401
from sillage.kernel.types import ModelTier

#: The two kinds of record AWS Agent Registry holds (specification 8.7).
KIND_MCP_SERVER = "mcp_server"
KIND_SKILL = "skill"

#: The states of a registry record: draft, approved, then deprecated.
STATE_DRAFT = "draft"
STATE_APPROVED = "approved"
STATE_DEPRECATED = "deprecated"


class OfflineModels:
    """A deterministic ``ModelBackend``: no network, no dependency, no figure.

    **Provisional** - :class:`sillage.agents.models.OfflineModels` replaces it.

    It answers from a script (``answers``: a substring of the prompt mapped to the
    text to return) and answers **nothing** otherwise. An empty answer is not a
    shortcut: every caller of ``ModelBackend`` in the repository already has a
    deterministic fallback, and that fallback is the one implementation of the
    rule. A test or a demo that wants a model text injects it in ``answers``.

    Every call is recorded in :attr:`calls`, which is what makes the replay bench
    and the tests able to prove that a figure never came from a model.
    """

    def __init__(self, answers: Mapping[str, str] | None = None) -> None:
        self.answers: dict[str, str] = dict(answers or {})
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        prompt: str,
        system: str = "",
        tier: ModelTier = ModelTier.FAST,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        """The scripted answer of the first matching key, empty when none matches."""
        self.calls.append({"prompt": prompt, "system": system, "tier": tier, "json_schema": json_schema})
        for needle, answer in self.answers.items():
            if needle and needle in (prompt or ""):
                return answer
        return ""

    @property
    def prompts(self) -> list[str]:
        """Every prompt sent to the model, in order."""
        return [str(call["prompt"]) for call in self.calls]


@dataclass(frozen=True)
class RegistryRecord:
    """One record of the registry: an MCP server, or a skill and its folder."""

    record_id: str
    kind: str
    name: str
    endpoint: str = ""
    folder: dict[str, str] = field(default_factory=dict)
    state: str = STATE_DRAFT
    reason: str = ""
    created_at: datetime | None = None

    @property
    def approved(self) -> bool:
        """True when the record went through the approval step (8.7)."""
        return self.state == STATE_APPROVED


class LocalRegistry:
    """``Registry`` in memory: the offline twin of AWS Agent Registry.

    **Provisional** - it exists because the specification asks the demo to show a
    skill exported, approved and then deprecated (journey 8.7, R18), and no other
    module owns the port yet. Records are readable, which is the whole point: the
    portal and the tests look at what was registered.
    """

    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock
        self._records: dict[str, RegistryRecord] = {}

    # -- Registry ----------------------------------------------------------- #
    def register_mcp_server(self, *, name: str, endpoint: str) -> str:
        """Register the MCP server of the gateway; return its record id."""
        record = RegistryRecord(
            record_id=self._id("mcp", name, endpoint),
            kind=KIND_MCP_SERVER,
            name=name,
            endpoint=endpoint,
            state=STATE_APPROVED,
            created_at=self._now(),
        )
        return self._add(record)

    def register_skill(self, *, name: str, folder: dict[str, str], draft: bool = True) -> str:
        """Register a skill folder, as a draft unless the caller approves it."""
        state = STATE_DRAFT if draft else STATE_APPROVED
        ident = self._id("skill", name, *sorted(folder))
        return self._add(RegistryRecord(ident, KIND_SKILL, name, "", dict(folder), state, "", self._now()))

    def approve(self, record_id: str) -> None:
        """Submit a record to approval: it becomes discoverable by an MCP client."""
        self._mutate(record_id, state=STATE_APPROVED, reason="")

    def deprecate(self, record_id: str, reason: str = "") -> None:
        """Deprecate a record, the way a fiche set aside deprecates its skill."""
        self._mutate(record_id, state=STATE_DEPRECATED, reason=reason)

    # -- reads -------------------------------------------------------------- #
    def records(self, *, kind: str = "") -> tuple[RegistryRecord, ...]:
        """Every record, sorted by id so two runs read the same order."""
        kept = (record for record in self._records.values() if not kind or record.kind == kind)
        return tuple(sorted(kept, key=lambda record: record.record_id))

    def skills(self) -> tuple[RegistryRecord, ...]:
        """The skill records only."""
        return self.records(kind=KIND_SKILL)

    def mcp_servers(self) -> tuple[RegistryRecord, ...]:
        """The MCP server records only."""
        return self.records(kind=KIND_MCP_SERVER)

    def get(self, record_id: str) -> RegistryRecord | None:
        """One record by id, ``None`` when the registry does not hold it."""
        return self._records.get(record_id)

    def __len__(self) -> int:
        """Number of records registered so far."""
        return len(self._records)

    # -- internals ---------------------------------------------------------- #
    def _add(self, record: RegistryRecord) -> str:
        """Store a record under its own id and return that id."""
        self._records[record.record_id] = record
        return record.record_id

    def _mutate(self, record_id: str, **changes: str) -> None:
        """Change the state of one record; an unknown id is an error."""
        found = self._records.get(record_id)
        if found is None:
            raise NotFound(f"enregistrement de registre inconnu : {record_id}", record_id=record_id)
        self._records[record_id] = replace(found, **changes)

    @staticmethod
    def _id(kind: str, *parts: str) -> str:
        """A deterministic record id: registering twice gives one record."""
        return make_id("skill", kind, *parts)

    def _now(self) -> datetime | None:
        """The instant of the record, ``None`` when no clock was injected."""
        return self.clock.now() if self.clock is not None else None


__all__ = [
    "KIND_MCP_SERVER",
    "KIND_SKILL",
    "STATE_APPROVED",
    "STATE_DEPRECATED",
    "STATE_DRAFT",
    "LocalRegistry",
    "OfflineModels",
    "RegistryRecord",
]
