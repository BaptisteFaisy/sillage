"""How a refusal reaches the caller: returned, never raised (ENF-IA-03).

Layer 1 decides before the tool runs. When it refuses, the caller must learn
it - the engineer, the agent, the MCP client - and the tool must not break the
call that asked. So every tool catches the refusal and returns it as a payload:
a stable code, a French message, and the rule that refused.

A refusal is a normal answer, not an accident: the policy engine writes it in
the journal, and the caller sees it in the trace of its own session.

The degradation is a different thing: a backend that is down (ENF-DISP-02,
R21) is not a refusal - the call was allowed, and the answer is simply empty,
with a note that says what was missing.
"""

from __future__ import annotations

from typing import Any

from sillage.kernel.errors import BackendUnavailable, SillageError

#: The key every refusal carries, whatever the tool that produced it.
ERROR_KEY = "error"


def tool_error(exc: SillageError) -> dict[str, Any]:
    """The refusal as the caller sees it: a stable code and a French message."""
    payload: dict[str, Any] = {
        ERROR_KEY: exc.code,
        "message": exc.message,
        "refused": True,
    }
    for key, value in exc.details.items():
        payload.setdefault(key, value)
    return payload


def is_refusal(payload: object) -> bool:
    """True when a tool answered with a refusal instead of its result."""
    return isinstance(payload, dict) and payload.get("refused") is True


def degraded(note: str) -> dict[str, Any]:
    """The answer of a tool when a backend is down: empty, and it says so.

    Sillage must never be the reason an incident stays open (ENF-DISP-02, R21):
    the caller goes on, with a note that tells it what was missing.
    """
    return {
        ERROR_KEY: BackendUnavailable.code,
        "message": note,
        "degraded": True,
        "refused": False,
    }


def quietly(action: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a write against a backend; a backend that is down must not break it.

    The answer a caller receives is worth more than the bookkeeping that comes
    with it: when the catalogue is unreachable, the consultation still answers
    what it could read.
    """
    try:
        return action(*args, **kwargs)
    except BackendUnavailable:
        return None


__all__ = ["ERROR_KEY", "degraded", "is_refusal", "quietly", "tool_error"]
