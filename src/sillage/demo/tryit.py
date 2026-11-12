"""The engine of the public try page: what the memory returns, and what it refuses.

Specification 19.1 asks for one page a judge can use without any account: the
judge describes a problem, picks a role and a client, and sees **both** what the
memory brings back **and** what it refuses. The second half is the point: the
isolation of the clients and the levels of trust are only credible when the
refusals are shown, with the rule that refused and the reason why.

The page does three things, in this order:

1. it builds the caller from a **demo token** - a role, and for an agent a mode
   and a client - and never from the text of the question (specification 10.4);
2. it runs the consultation of :mod:`sillage.demo.lookup`, so the fiches it shows
   carry the level, the source and the success rate the code computed;
3. it asks layer 1 about the calls the page must show as refused, and returns
   them in ``refusals``.

The answer is built **by code** (ENF-IA-01): no figure comes from a model, and an
autonomous agent never receives a content that is not validated (EF-RES-05).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from sillage.demo.lookup import consult
from sillage.demo.stack import Stack, build
from sillage.kernel.ids import usage_id
from sillage.kernel.protocols import Principal, ToolRequest
from sillage.kernel.types import AgentMode, Decision, PrincipalRole, Scope, ScopeKind

#: The roles the page offers, in French or English, with the mode they carry.
ROLES: dict[str, tuple[PrincipalRole, AgentMode | None]] = {
    "engineer": (PrincipalRole.ENGINEER, None),
    "ingénieur": (PrincipalRole.ENGINEER, None),
    "referent": (PrincipalRole.REFERENT, None),
    "référent": (PrincipalRole.REFERENT, None),
    "admin": (PrincipalRole.ADMIN, None),
    "administrateur": (PrincipalRole.ADMIN, None),
    "team_lead": (PrincipalRole.TEAM_LEAD, None),
    "chef d'équipe": (PrincipalRole.TEAM_LEAD, None),
    "agent": (PrincipalRole.AGENT, AgentMode.AUTONOMOUS),
    "agent autonome": (PrincipalRole.AGENT, AgentMode.AUTONOMOUS),
    "agent_autonomous": (PrincipalRole.AGENT, AgentMode.AUTONOMOUS),
    "agent assisté": (PrincipalRole.AGENT, AgentMode.ASSISTED),
    "agent_assisted": (PrincipalRole.AGENT, AgentMode.ASSISTED),
}

#: The internal perimeter, as the page spells it.
INTERNAL = "interne"

#: The tools the page probes to show the refusals, and what each would do.
PROBES: tuple[tuple[str, str], ...] = (
    ("recall_all", "lire aussi le contenu non validé"),
    ("validate_fiche", "valider une fiche"),
    ("publish_common", "publier dans le savoir commun"),
)


@lru_cache(maxsize=1)
def _stack() -> Stack:
    """The stack of the page, built once: the page only reads it."""
    return build()


def reset() -> None:
    """Forget the stack of the page: the next question builds a fresh one."""
    _stack.cache_clear()


def ask(problem: str, role: str, client_id: str) -> dict[str, Any]:
    """What the memory answers to a problem, and what it refuses to answer.

    ``role`` is one of :data:`ROLES`, ``client_id`` the client the judge picked.
    The answer holds the served fiches with their level, their source and their
    success rate, the common knowledge, and the refusals with the rule that
    refused.
    """
    stack = _stack()
    principal = principal_of(stack, role, client_id)
    found = consult(stack, principal=principal, problem=problem, scope=perimeter_of(client_id))
    refusals = _refusals(stack, principal, client_id)
    return _answer(problem, role, client_id, principal, found, refusals)


def principal_of(stack: Stack, role: str, client_id: str) -> Principal:
    """The caller of the page: a demo token, and nothing else."""
    wanted = ROLES.get(str(role).strip().lower(), ROLES["engineer"])
    principal_role, mode = wanted
    home = client_id if client_id and client_id != INTERNAL else None
    return stack.principal(principal_role, home, mode)


def perimeter_of(client_id: str) -> Scope:
    """The perimeter the question is asked in: a client, or the internal one."""
    if client_id and client_id != INTERNAL:
        return Scope(kind=ScopeKind.CLIENT, client_id=client_id)
    return Scope(kind=ScopeKind.INTERNAL)


def _refusals(stack: Stack, principal: Principal, client_id: str) -> list[dict[str, str]]:
    """Every call layer 1 refuses, with the rule that refused and the reason."""
    refusals: list[dict[str, str]] = []
    for request in _probes(stack, principal, client_id):
        authorization = stack.decide(request.tool, request.principal, **request.arguments)
        if authorization.decision is Decision.PERMIT:
            continue
        refusals.append(
            {
                "outil": request.tool,
                "regle": authorization.rule,
                "raison": authorization.reason,
                "decision": authorization.decision.value,
            }
        )
    return refusals


def _probes(stack: Stack, principal: Principal, client_id: str) -> list[ToolRequest]:
    """The calls the page shows as refused: the unvalidated content, the other
    client, and the two tools a referent alone may use."""
    probes = [stack.request(tool, principal, clientId=client_id, ficheId="f-demo") for tool, _ in PROBES]
    other = _other_client(stack, client_id)
    if other and principal.client_id:
        probes.append(stack.request("recall", principal, clientId=other))
    return probes


def _other_client(stack: Stack, client_id: str) -> str:
    """The client the page tries to read from the wrong side: the other one."""
    for client in stack.clients():
        if client.client_id != client_id:
            return client.client_id
    return ""


def _answer(
    problem: str,
    role: str,
    client_id: str,
    principal: Principal,
    found: Any,
    refusals: list[dict[str, str]],
) -> dict[str, Any]:
    """The answer of the page: the text, the fiches, the common, the refusals."""
    return {
        "probleme": problem,
        "role": role,
        "client": client_id or INTERNAL,
        "intention": found.intent,
        "reponse": _text(found),
        "fiches": list(found.fiches),
        "commun": list(found.common),
        "refusals": refusals,
        "note": _note(found.hidden, principal),
        "usage_id": usage_id(principal.subject, "", problem),
    }


def _text(found: Any) -> str:
    """The answer a judge reads, written by code from what was served."""
    if not found.served_count:
        return (
            "Aucune fiche validée ne répond à ce problème dans ce périmètre. "
            "La mémoire ne renvoie rien de non validé."
        )
    first = (found.fiches or found.common)[0]
    steps = first.get("etapes") or []
    head = f"{found.served_count} fiche(s) servie(s), la plus proche : « {first.get('titre')} »."
    if steps:
        head += f" Première étape : {steps[0]}"
    if found.hidden:
        head += f" {found.hidden} élément(s) non validé(s) ont été écartés."
    return head


def _note(hidden: int, principal: Principal) -> str:
    """The line a judge reads under the answer: what was withheld, and why."""
    if hidden and principal.autonomous:
        return f"{hidden} élément(s) non validé(s) existent ; ils ne sont pas servis à un agent autonome."
    if hidden:
        return f"{hidden} élément(s) non validé(s) existent ; ils ne sont pas servis à un agent."
    return "Tout ce qui a été servi est validé par un référent."


__all__ = ["INTERNAL", "PROBES", "ROLES", "ask", "perimeter_of", "principal_of", "reset"]
