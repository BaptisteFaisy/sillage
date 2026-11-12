"""One policy decision, written in the journal.

Chapter 9.10 (EF-ADM-02) and ENF-SEC-05 ask for a journal nobody can edit
afterwards, holding every tool call and every policy decision. The entry built
here carries the caller, the tool, the decision, the reason and the perimeter -
enough to show, after the fact, that a refusal happened **before** the tool ran
(R9) and to name the rule that decided it (ENF-IA-03).

Two choices keep the journal honest:

* the caller is a **pseudonym** (``kernel.ids.pseudonym``): the journal is a log
  of calls, not a scoreboard of people (ENF-RH-01);
* the perimeter is the one the call points at, so an attempt to read client A
  with a token of client B is written as it happened.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sillage.kernel.ids import journal_id, pseudonym
from sillage.kernel.protocols import Authorization, Clock, ToolRequest
from sillage.kernel.types import JournalEntry, Scope, ScopeKind

#: Perimeter of a caller that carries no client: the MSP itself (12.5).
INTERNAL = Scope(kind=ScopeKind.INTERNAL).actor_id


def caller_of(request: ToolRequest) -> str:
    """The caller as the journal writes it: a pseudonym, never an identity."""
    return pseudonym(request.principal.subject)


def scope_of(request: ToolRequest) -> str:
    """The perimeter the call points at.

    The requested client when the call names one - that is what an auditor
    looks for - and the perimeter of the token otherwise.
    """
    asked = request.client_id
    if asked:
        return Scope(kind=ScopeKind.CLIENT, client_id=asked).actor_id
    home = request.principal.home_scope()
    return home.actor_id if home is not None else INTERNAL


def reason_of(authorization: Authorization) -> str:
    """The reason a reader understands: the rule id, then what it says."""
    if not authorization.rule:
        return authorization.reason
    return f"{authorization.rule} — {authorization.reason}"


def entry(
    *,
    request: ToolRequest,
    authorization: Authorization,
    usage_id: str = "",
    work_item_id: str = "",
    clock: Clock | None = None,
) -> JournalEntry:
    """The journal entry of one decision, allowed or refused.

    ``clock`` keeps the tests reproducible; without it the entry is dated by the
    machine clock, which is what the deployed gateway does.
    """
    at = clock.now() if clock is not None else datetime.now(UTC)
    caller = caller_of(request)
    nonce = f"{authorization.rule}:{authorization.decision.value}:{usage_id}{work_item_id}"
    return JournalEntry(
        entry_id=journal_id(at.isoformat(), caller, request.tool, nonce),
        at=at,
        caller=caller,
        caller_role=request.principal.role,
        tool=request.tool,
        decision=authorization.decision,
        reason=reason_of(authorization),
        scope=scope_of(request),
        usage_id=usage_id,
        work_item_id=work_item_id,
    )


__all__ = ["INTERNAL", "caller_of", "entry", "reason_of", "scope_of"]
