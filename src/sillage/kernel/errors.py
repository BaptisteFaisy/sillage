"""Errors of Sillage.

Two rules shape this module:

* a refusal must always say **why** and be logged (ENF-IA-03) - so every
  error carries a machine readable ``code`` next to the human message;
* Sillage must never be a single point of failure (ENF-DISP-02) - so backend
  failures raise :class:`BackendUnavailable`, which callers turn into an empty
  answer instead of an exception shown to an engineer mid-incident.
"""

from __future__ import annotations


class SillageError(Exception):
    """Base class. ``code`` is stable and appears in the journal."""

    code = "sillage_error"

    def __init__(self, message: str, *, code: str | None = None, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.code
        self.details = details

    def as_dict(self) -> dict[str, object]:
        return {"error": self.code, "message": self.message, **self.details}


class PolicyDenied(SillageError):
    """Layer 1 refused the call before any tool ran (specification 10.1)."""

    code = "policy_denied"

    def __init__(self, message: str, *, rule: str = "", tool: str = "", **details: object) -> None:
        super().__init__(message, rule=rule, tool=tool, **details)
        self.rule = rule
        self.tool = tool


class NotFound(SillageError):
    """Unknown fiche, episode, lesson, usage or client."""

    code = "not_found"


class DuplicateTrace(SillageError):
    """The same trace was received twice (EF-CAP-11): handled, not an error."""

    code = "duplicate_trace"


class ValidationError(SillageError):
    """Input rejected by a code control, before any model sees it."""

    code = "validation_error"


class UnsourcedClaim(SillageError):
    """A fiche statement cites no episode (EF-VAL-02). Never publishable."""

    code = "unsourced_claim"


class LeakDetected(SillageError):
    """A client identifier survived the rewrite of a common lesson (EF-GEN-03)."""

    code = "leak_detected"

    def __init__(self, message: str, *, identifiers: tuple[str, ...] = (), **details: object) -> None:
        super().__init__(message, **details)
        self.identifiers = identifiers


class SecretDetected(SillageError):
    """Raised only when a secret survived redaction - a bug, not an input."""

    code = "secret_detected"


class PoisonedContent(SillageError):
    """Captured text tries to instruct a future agent (specification 10.3)."""

    code = "poisoned_content"


class ScopeNotAllowed(SillageError):
    """A caller tried to read or write outside its perimeter."""

    code = "scope_not_allowed"


class CommonSharingRefused(SillageError):
    """The client contract says "no sharing" (EF-GEN-06)."""

    code = "common_sharing_refused"


class LevelNotAllowed(SillageError):
    """Content not validated enough for this caller (EF-RES-05)."""

    code = "level_not_allowed"


class BackendUnavailable(SillageError):
    """Memory, catalogue or model is down: callers degrade, they do not fail."""

    code = "backend_unavailable"


class QuotaExceeded(SillageError):
    """A budget was reached (model calls, batch size, replay attempts)."""

    code = "quota_exceeded"
