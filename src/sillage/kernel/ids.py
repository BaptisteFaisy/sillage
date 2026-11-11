"""Identifiers of Sillage: short, prefixed, deterministic.

Deterministic ids are what makes capture idempotent (EF-CAP-11): the same
trace replayed twice produces the same fingerprint, the same session id and
therefore the same episode, instead of a duplicate.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

_PREFIXES = {
    "work": "w",
    "trace": "t",
    "episode": "ep",
    "lesson": "le",
    "fiche": "f",
    "usage": "u",
    "journal": "j",
    "skill": "sk",
    "common": "co",
}

_SLUG_KEEP = re.compile(r"[^a-z0-9]+")


def _digest(*parts: str, length: int = 6) -> str:
    """Stable short digest of the given parts."""
    payload = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:length]


def make_id(kind: str, *parts: str) -> str:
    """Build ``<prefix>-<digest>`` from the identifying parts of an object."""
    prefix = _PREFIXES.get(kind, kind[:2])
    return f"{prefix}-{_digest(kind, *parts)}"


def work_item_id(source: str, external_id: str) -> str:
    """Identity of a finished job: its source plus its own reference."""
    return f"{source}:{external_id}"


def trace_fingerprint(work_item_id_: str, payload: str) -> str:
    """Deduplication key of one capture (EF-CAP-11)."""
    return _digest("fingerprint", work_item_id_, payload, length=16)


def session_id(work_item_id_: str) -> str:
    """AgentCore Memory session id: one session per finished job."""
    return work_item_id_


def episode_id(scope_actor: str, work_item_id_: str) -> str:
    return make_id("episode", scope_actor, work_item_id_)


def lesson_id(scope_actor: str, *episode_ids: str) -> str:
    return make_id("lesson", scope_actor, *sorted(episode_ids))


def fiche_id(scope_actor: str, title: str, lesson: str = "") -> str:
    return make_id("fiche", scope_actor, lesson or slug(title))


def usage_id(caller: str, moment: str, problem: str) -> str:
    return make_id("usage", caller, moment, problem)


def journal_id(moment: str, caller: str, tool: str, nonce: str = "") -> str:
    return make_id("journal", moment, caller, tool, nonce)


def slug(text: str, max_length: int = 64) -> str:
    """Filesystem and skill safe slug (``connexions-base-epuisees``)."""
    folded = unicodedata.normalize("NFKD", text or "")
    ascii_only = "".join(c for c in folded if not unicodedata.combining(c))
    lowered = ascii_only.lower().encode("ascii", "ignore").decode("ascii")
    cleaned = _SLUG_KEEP.sub("-", lowered).strip("-")
    return cleaned[:max_length].rstrip("-") or "sans-titre"


def pseudonym(identity: str, salt: str = "sillage") -> str:
    """Pseudonymise an author (specification 17.1: no per-person metric)."""
    return f"p-{_digest(salt, identity, length=4)}"


def is_prefixed(value: str, kind: str) -> bool:
    """Cheap shape check used by the input validation of the tools."""
    prefix = _PREFIXES.get(kind)
    return bool(prefix) and value.startswith(f"{prefix}-")
