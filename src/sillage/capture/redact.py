"""Secrets removal, before anything is stored (EF-CAP-03, chapter 17.3).

A captured trace is untrusted text: a ticket, a journal extract, a command
output. Before a single byte reaches AgentCore Memory, the object store or the
catalogue, every well known secret shape is replaced by an explicit marker.

Three properties are wanted here, and they shape the code:

* **explicit** - the replacement says which family was removed
  (``[REDACTED:aws_access_key]``), so a reader of the episode is not fooled by
  a hole in the text;
* **not reversible** - the secret itself never survives anywhere, not even in
  a digest: nothing is stored, nothing is hashed, nothing is kept aside;
* **counted** - the number of removed secrets is returned, so the capture can
  report it and so the tests can prove nothing slipped through (R2).

No model is called: a secret must never be handed to a model that could quote
it back, and the redaction must give the same result on every machine.
"""

from __future__ import annotations

import re
from dataclasses import replace

from sillage.kernel.types import Trace

#: One secret family: its label and the pattern that finds it.
LabeledPattern = tuple[str, re.Pattern[str]]


def _family(name: str, expression: str, *, ignore_case: bool = False) -> LabeledPattern:
    """Compile one secret family."""
    return name, re.compile(expression, re.IGNORECASE if ignore_case else 0)


#: Every family Sillage knows, in the order they are applied. The order
#: matters: a private key and a connection string are removed before the
#: keyword based families, so a password inside a URL is not half redacted.
FAMILIES: tuple[LabeledPattern, ...] = (
    _family(
        "private_key",
        r"-----BEGIN(?: [A-Z]+)* PRIVATE KEY-----[\s\S]*?-----END(?: [A-Z]+)* PRIVATE KEY-----",
    ),
    _family(
        "connection_string",
        r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:@/]+:[^\s@/]+@[^\s)\]},;\"']+",
    ),
    _family("jwt", r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}"),
    _family("aws_access_key", r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|ASCA)[0-9A-Z]{16}\b"),
    _family("github_token", r"\bgh[pousr]_[A-Za-z0-9]{16,}"),
    _family("gitlab_token", r"\bglpat-[A-Za-z0-9_\-]{16,}"),
    _family("slack_token", r"\bxox[baprse]-[A-Za-z0-9\-]{10,}"),
    _family("bearer_token", r"\bbearer\s+[A-Za-z0-9._\-]{12,}", ignore_case=True),
    _family(
        "aws_secret_key",
        r"\baws_secret_access_key\b\s*[:=]\s*[\"']?[A-Za-z0-9/+=]{20,}[\"']?",
        ignore_case=True,
    ),
    _family(
        "password",
        r"\b(?:password|passwd|pwd|mot de passe)\b\s*[:=]\s*(?:\"[^\"]+\"|'[^']+'|[^\s,;]{4,})",
        ignore_case=True,
    ),
    _family(
        "generic_secret",
        r"\b(?:api[_\-]?key|apikey|secret[_\-]?key|client[_\-]?secret|access[_\-]?key"
        r"|auth[_\-]?token|secret|token)\b\s*[:=]\s*[\"']?[A-Za-z0-9_\-./+=]{8,}[\"']?",
        ignore_case=True,
    ),
)

#: The compiled patterns, in the same order (the surface the contract asks for).
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = tuple(pattern for _, pattern in FAMILIES)

#: Prefix of every replacement, so a reader spots a redaction at a glance.
MARKER = "[REDACTED:{name}]"


def marker(name: str) -> str:
    """The explicit, stable and not reversible replacement of one family."""
    return MARKER.format(name=name)


def redact(text: str) -> tuple[str, int]:
    """Remove every secret of ``text``; return the cleaned text and the count."""
    if not text:
        return text, 0
    removed = 0
    for name, pattern in FAMILIES:
        text, found = pattern.subn(lambda _match, label=name: marker(label), text)
        removed += found
    return text, removed


def redact_trace(trace: Trace) -> tuple[Trace, int]:
    """Remove the secrets of a whole trace: every event and every origin value."""
    removed = 0
    events = []
    for event in trace.events:
        cleaned, found = redact(event.text)
        removed += found
        events.append(replace(event, text=cleaned) if found else event)
    origin, origin_removed = redact_mapping(trace.origin)
    removed += origin_removed
    return replace(trace, events=tuple(events), origin=origin), removed


def redact_mapping(values: dict[str, str]) -> tuple[dict[str, str], int]:
    """Remove the secrets of a ``str -> str`` mapping; keep the key order."""
    removed = 0
    cleaned: dict[str, str] = {}
    for key, value in values.items():
        text, found = redact(str(value))
        removed += found
        cleaned[key] = text
    return cleaned, removed


def contains_secret(text: str) -> bool:
    """True when ``text`` still holds a secret. Used by the tests, never to store."""
    return any(pattern.search(text or "") for pattern in SECRET_PATTERNS)


__all__ = [
    "FAMILIES",
    "MARKER",
    "SECRET_PATTERNS",
    "contains_secret",
    "marker",
    "redact",
    "redact_mapping",
    "redact_trace",
]
