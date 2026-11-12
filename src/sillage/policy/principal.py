"""The caller of a tool, built from the token and from nothing else.

Chapter 10.1 puts the first guard-fou outside the agents: the gateway evaluates
every call against the **claims of the token**, before any tool runs. This
module is the only place where a ``Principal`` is built, and it reads nothing
but the claims - the body of the call never reaches it (specification 10.4,
R9 and R15).

A token that carries no usable role gives a principal that **matches no rule**:
the engine then refuses by default. Such a token is treated as an agent without
a client and without a mode - the strictest shape of the specification (12.6) -
so a malformed token can never be mistaken for a human one.

The subject of a principal is the identity carried by the token. It is never
written as it is: the journal and the catalogue store its pseudonym
(``kernel.ids.pseudonym``), because no figure about a person may exist
(ENF-RH-01).
"""

from __future__ import annotations

from typing import Any

from sillage.kernel.ids import pseudonym
from sillage.kernel.protocols import Principal
from sillage.kernel.types import AgentMode, PrincipalRole

#: Claim names an identity provider may use for the role (specification 12.6).
ROLE_CLAIMS: tuple[str, ...] = ("role", "custom:role", "principalType", "roles")
#: Claim names for the agent mode: ``autonomous`` or ``assisted``.
MODE_CLAIMS: tuple[str, ...] = ("mode", "custom:mode", "agentMode")
#: Claim names for the client of an agent token.
CLIENT_CLAIMS: tuple[str, ...] = ("clientId", "custom:clientId", "client")
#: Claim names for the identity of the caller.
SUBJECT_CLAIMS: tuple[str, ...] = ("sub", "subject", "principalId")

#: Accepted spellings of a role: the value of the kernel.
ROLE_VALUES: dict[str, PrincipalRole] = {role.value: role for role in PrincipalRole}

#: Accepted spellings of a mode: the value of the kernel, and the French of the
#: prose of the specification (``autonome``, ``assisté``).
MODE_VALUES: dict[str, AgentMode] = {
    **{mode.value: mode for mode in AgentMode},
    "autonome": AgentMode.AUTONOMOUS,
    "assisté": AgentMode.ASSISTED,
    "assiste": AgentMode.ASSISTED,
}


def _normalise(value: str) -> str:
    """A claim value read the same way whatever the spelling."""
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _claim(claims: dict[str, Any], names: tuple[str, ...]) -> str | None:
    """The first claim of ``names`` that carries a non-empty string."""
    for name in names:
        value = claims.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def role_of(claims: dict[str, Any]) -> PrincipalRole | None:
    """The role carried by the token, ``None`` when it carries none."""
    for name in ROLE_CLAIMS:
        value = claims.get(name)
        candidates = value if isinstance(value, (list, tuple)) else [value]
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            role = ROLE_VALUES.get(_normalise(candidate))
            if role is not None:
                return role
    return None


def mode_of(claims: dict[str, Any]) -> AgentMode | None:
    """The mode carried by the token, ``None`` when it carries none."""
    value = _claim(claims, MODE_CLAIMS)
    if value is None:
        return None
    return MODE_VALUES.get(_normalise(value))


def client_of(claims: dict[str, Any]) -> str | None:
    """The client carried by the token, ``None`` when it carries none."""
    return _claim(claims, CLIENT_CLAIMS)


def subject_of(claims: dict[str, Any]) -> str:
    """The identity of the caller, or a pseudonym built from the claims."""
    raw = _claim(claims, SUBJECT_CLAIMS)
    if raw is not None:
        return raw
    role = role_of(claims)
    client = client_of(claims)
    return pseudonym(f"{role.value if role else 'role-less'}:{client or 'msp'}")


def from_token(claims: dict[str, Any]) -> Principal:
    """Build the caller of a tool from the claims of its token - and only them.

    Nothing of the call reaches this function: the client of an agent comes
    from the token, never from the body of the request (specification 10.4).
    A token without a usable role gives a principal that no rule can match,
    so the engine refuses by default.
    """
    role = role_of(claims)
    subject = subject_of(claims)
    if role is None:
        return Principal(subject=subject, role=PrincipalRole.AGENT, client_id=None, mode=None)
    return Principal(
        subject=subject,
        role=role,
        client_id=client_of(claims),
        mode=mode_of(claims),
    )


def demo_token(
    role: PrincipalRole, client_id: str | None = None, mode: AgentMode | None = None
) -> dict[str, Any]:
    """A demo token, for the public "try it" page and for the tests.

    The claims are the ones of chapter 12.6: ``role``, and for an agent
    ``mode`` and ``clientId``. The ``author`` claim carries the pseudonym of
    the caller, so the deployed policy can compare it with the author of the
    work an agent asks to forget (ENF-RH-01).
    """
    subject = f"demo-{role.value}-{client_id or 'msp'}"
    claims: dict[str, Any] = {"sub": subject, "role": role.value, "author": pseudonym(subject)}
    if client_id:
        claims["clientId"] = client_id
    if mode is not None:
        claims["mode"] = mode.value
    elif role is PrincipalRole.AGENT:
        claims["mode"] = AgentMode.AUTONOMOUS.value
    return claims


__all__ = [
    "CLIENT_CLAIMS",
    "MODE_CLAIMS",
    "MODE_VALUES",
    "ROLE_CLAIMS",
    "ROLE_VALUES",
    "SUBJECT_CLAIMS",
    "client_of",
    "demo_token",
    "from_token",
    "mode_of",
    "role_of",
    "subject_of",
]
