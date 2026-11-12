"""Fixtures and builders shared by the four test modules of the guards.

The definitive test sets are produced later, as data, under ``data/fixtures/``
(``planted_secrets.json``, ``poisoned_tickets.json``,
``generalisation_set.json``). This module is the single place that knows how to
read them - and what to do when they are **not there yet**: every test module
passes its own inline fallback set, so the suite proves the guards from the
first commit and keeps proving them, unchanged, once the data lands.

The loader is deliberately tolerant about the shape of the file: a bare list, a
list under one of the usual keys, strings or objects, and the usual synonyms for
``text`` and ``id``. A fixture written by another agent must not break a test
that already passes - but a shape it cannot read falls back to the inline set,
which is the safe direction.

Also here, so that the four modules share one vocabulary:

* :class:`ScriptedGuardrails` - a ``GuardrailFilter`` that answers from a script
  and records what it was asked to scan (no boto3, no network);
* the builders of a client, a trace, a fiche and a claim.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sillage.kernel.protocols import GuardrailVerdict
from sillage.kernel.types import (
    AuthorKind,
    Claim,
    Client,
    Fiche,
    Level,
    Role,
    Scope,
    ScopeKind,
    Source,
    Trace,
    TraceEvent,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "data" / "fixtures"

#: The fixed "now" of the guards tests: a reproducible suite.
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)

#: Keys a fixture may use for the list itself.
LIST_KEYS = ("items", "entries", "cases", "secrets", "tickets", "fiches", "set", "data", "records")

#: Synonyms the loader accepts for the text of an entry.
TEXT_KEYS = ("text", "content", "payload", "body", "ticket", "trace", "value", "sample", "input")

#: Synonyms the loader accepts for the identifier of an entry.
ID_KEYS = ("id", "name", "ref", "key", "label", "titre", "title")


def load_fixture(name: str, fallback: Sequence[Any]) -> list[Any]:
    """The committed fixture ``data/fixtures/<name>``, or the inline fallback."""
    path = FIXTURES / name
    if not path.exists():
        return list(fallback)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return list(fallback)
    entries = _list_of(raw)
    return entries or list(fallback)


def entry_text(entry: Any, extra_keys: Sequence[str] = ()) -> str:
    """The text of one fixture entry, whatever key it is stored under."""
    if isinstance(entry, str):
        return entry
    if not isinstance(entry, dict):
        return ""
    for key in (*extra_keys, *TEXT_KEYS):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def entry_id(entry: Any, index: int, extra_keys: Sequence[str] = ()) -> str:
    """The identifier of one fixture entry, or its position in the set."""
    if isinstance(entry, dict):
        for key in (*extra_keys, *ID_KEYS):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return f"{index + 1:02d}"


def entry_field(entry: Any, keys: Sequence[str], default: Any = "") -> Any:
    """The first present field among ``keys``."""
    if not isinstance(entry, dict):
        return default
    for key in keys:
        value = entry.get(key)
        if value not in (None, "", []):
            return value
    return default


def _list_of(raw: Any) -> list[Any]:
    """The list of entries of a fixture file, whatever wraps it."""
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, dict):
        for key in LIST_KEYS:
            value = raw.get(key)
            if isinstance(value, list) and value:
                return list(value)
    return []


class ScriptedGuardrails:
    """A ``GuardrailFilter`` that answers from a script: no boto3, no network."""

    def __init__(
        self,
        *,
        blocked: bool = False,
        masked_text: str = "",
        reasons: Sequence[str] = (),
    ) -> None:
        self.blocked = blocked
        self.masked_text = masked_text
        self.reasons = tuple(reasons)
        self.scanned: list[str] = []

    def scan(self, text: str) -> GuardrailVerdict:
        """Record the text, answer what the script says."""
        self.scanned.append(text)
        if self.blocked:
            return GuardrailVerdict(
                blocked=True, action="blocked", reasons=self.reasons or ("SENSITIVE_DATA",)
            )
        if self.masked_text:
            return GuardrailVerdict(
                action="masked", masked_text=self.masked_text, reasons=self.reasons or ("PII",)
            )
        return GuardrailVerdict()


def make_client(client_id: str = "a", name: str = "Client A - retail", *, no_common: bool = False) -> Client:
    """One tenant of the MSP."""
    return Client(
        client_id=client_id,
        name=name,
        no_common=no_common,
        technologies=("postgresql", "ecs"),
    )


def make_event(
    text: str, *, role: Role = Role.ASSISTANT, kind: str = "action", author: str = "p-7f3a"
) -> TraceEvent:
    """One dated element of a normalised trace."""
    return TraceEvent(role=role, text=text, author=author, author_kind=AuthorKind.HUMAN, at=NOW, kind=kind)


def make_trace(
    texts: Sequence[str],
    *,
    client_id: str = "a",
    work_item_id: str = "guichet:ticket-9001",
    author: str = "p-7f3a",
    closing: str = "[système] Ticket clos. Résolution confirmée par le demandeur.",
) -> Trace:
    """A finished job: a request, the given actions, an explicit closing."""
    events = [make_event(texts[0], role=Role.USER, kind="request", author=author)]
    events.extend(make_event(text) for text in texts[1:])
    events.append(make_event(closing, role=Role.OTHER, kind="system", author=author))
    return Trace(
        work_item_id=work_item_id,
        scope=Scope(kind=ScopeKind.CLIENT, client_id=client_id),
        source=Source.GUICHET,
        author_kind=AuthorKind.HUMAN,
        author=author,
        technologies=("postgresql", "ecs"),
        closed_at=NOW,
        events=tuple(events),
        origin={"ticket": work_item_id.split(":", 1)[-1], "code": "INC-500"},
    )


def make_claim(
    text: str = "Le pool de connexions doit rester à 10.",
    episodes: Sequence[str] = ("ep-1a2b3c",),
) -> Claim:
    """One statement of a fiche, citing its episodes."""
    return Claim(text=text, episode_ids=tuple(episodes))


def make_fiche(
    *,
    scope: Scope | None = None,
    fiche_id: str = "f-1a2b3c",
    title: str = "Connexions à la base épuisées après un déploiement",
    body: str = "Le déploiement a multiplié le pool de connexions par instance.",
    claims: Sequence[Claim] = (),
    sources: Sequence[str] = ("guichet:ticket-2041",),
    level: Level = Level.VALIDATED,
    validated_by: str = "p-refer",
) -> Fiche:
    """A fiche reviewed by a referent, inside one client perimeter."""
    return Fiche(
        fiche_id=fiche_id,
        scope=scope or Scope(kind=ScopeKind.CLIENT, client_id="a"),
        title=title,
        body=body,
        claims=tuple(claims) or (make_claim(),),
        sources=tuple(sources),
        level=level,
        validated_by=validated_by,
        validated_at=NOW,
        technologies=("postgresql", "ecs"),
    )


def all_text(*parts: Any) -> str:
    """Every string of the given objects, joined: what a test searches in."""
    return "\n".join(str(part) for part in parts if part is not None)


#: What replaces an identifier when a test needs a rewritten, general version.
GENERIC_PLACEHOLDER = "[anonymisé]"


def generalize_text(text: str, identifiers: Iterable[str]) -> str:
    """Rewrite ``text`` without any identifier: the generalisation a test needs.

    Longest identifier first, so that an ARN is replaced whole instead of
    leaving its account id behind. The placeholder is neutral on purpose: it
    cannot itself be mistaken for a client name.
    """
    result = text
    ordered = sorted({str(item) for item in identifiers if str(item).strip()}, key=len, reverse=True)
    for identifier in ordered:
        result = re.sub(re.escape(identifier), GENERIC_PLACEHOLDER, result, flags=re.IGNORECASE)
    return result


__all__ = [
    "FIXTURES",
    "GENERIC_PLACEHOLDER",
    "ID_KEYS",
    "LIST_KEYS",
    "NOW",
    "TEXT_KEYS",
    "ScriptedGuardrails",
    "all_text",
    "entry_field",
    "entry_id",
    "entry_text",
    "generalize_text",
    "load_fixture",
    "make_claim",
    "make_client",
    "make_event",
    "make_fiche",
    "make_trace",
]
