"""The policies of Sillage, as data.

Chapter 10.4 lists the rules, annex D gives five of them in the Cedar syntax of
AgentCore Policy, and the table of chapter 10.5 says who may do what. This
module turns those three tables into **one list of rules**, each of them a
record: an id, an effect, an action, a predicate, the reason a judge can read,
and the Cedar condition of the deployed policy.

The same list feeds three consumers, so they cannot drift apart:

* :mod:`sillage.policy.engine` decides with the Python predicates;
* :mod:`sillage.policy.cedar` renders the Cedar text of the gateway;
* the tests walk the table 10.5 and check both directions.

Rules are read in order and **an interdiction always wins** over an
authorisation (specification 10.1). Nothing here reads the body of a call
beyond its parameters: the client of the caller comes from its token, and the
engine never calls a model, a backend or the network.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sillage.kernel.ids import pseudonym
from sillage.kernel.protocols import ToolRequest
from sillage.kernel.types import AgentMode, Decision, PrincipalRole

#: The eleven tools the gateway exposes (specification 15.1).
TOOLS: tuple[str, ...] = (
    "recall",
    "recall_all",
    "get_item",
    "record_episode",
    "report_outcome",
    "propose_fiche",
    "validate_fiche",
    "publish_common",
    "set_aside",
    "export_skill",
    "forget",
)

#: The tools whose call carries a ``clientId`` (specification 15.1).
CLIENT_TOOLS: tuple[str, ...] = ("recall", "recall_all", "record_episode")

#: Roles that read the memory of the MSP (chapter 7). ``team_lead`` reads
#: indicators by topic and by client, never by person (ENF-RH-01): it has no
#: column in the table 10.5, so it keeps the read-only tools.
READER_ROLES: tuple[PrincipalRole, ...] = (
    PrincipalRole.ENGINEER,
    PrincipalRole.REFERENT,
    PrincipalRole.ADMIN,
    PrincipalRole.TEAM_LEAD,
)

#: Roles that may propose a fiche (table 10.5: engineer, referent, admin).
AUTHOR_ROLES: tuple[PrincipalRole, ...] = (
    PrincipalRole.ENGINEER,
    PrincipalRole.REFERENT,
    PrincipalRole.ADMIN,
)

#: Every role a token of the MSP can carry.
KNOWN_ROLES: tuple[PrincipalRole, ...] = tuple(PrincipalRole)


@dataclass(frozen=True)
class Rule:
    """One policy line: what it forbids or permits, and why.

    ``when`` reads the call as the gateway sees it - the principal built from
    the token, and the parameters of the call. ``cedar`` is the same condition
    in the syntax of AgentCore Policy, rendered by :mod:`sillage.policy.cedar`;
    an empty ``cedar`` means the rule has no deployed twin (the gateway only
    exposes the tools of :data:`TOOLS`, so "unknown tool" needs none).
    """

    id: str
    effect: Decision
    action: str
    when: Callable[[ToolRequest], bool]
    why: str
    cedar: str = ""
    cedar_clause: str = "when"
    cedar_actions: tuple[str, ...] = ()


# -- predicates ------------------------------------------------------------- #
def _has_role(request: ToolRequest, roles: Sequence[PrincipalRole]) -> bool:
    """The caller carries one of these roles."""
    return request.principal.role in roles


def _is_agent(request: ToolRequest) -> bool:
    """The caller is an agent, whatever its mode."""
    return request.principal.role is PrincipalRole.AGENT


def _is_autonomous(request: ToolRequest) -> bool:
    """The caller is an agent working without a human relecture."""
    return _is_agent(request) and request.principal.mode is AgentMode.AUTONOMOUS


def _is_assisted(request: ToolRequest) -> bool:
    """The caller is an agent whose work a human reviews."""
    return _is_agent(request) and request.principal.mode is AgentMode.ASSISTED


def _of_own_client(request: ToolRequest) -> bool:
    """The call names the client of the token - or names no client at all."""
    return request.client_id == request.principal.client_id


def _names_own_client(request: ToolRequest) -> bool:
    """The call names a client, and that client is the one of the token."""
    return request.client_id is not None and _of_own_client(request)


def _crosses_clients(request: ToolRequest) -> bool:
    """The call asks for a client the token does not carry (specification 10.4)."""
    token_client = request.principal.client_id
    asked = request.client_id
    return token_client is not None and asked is not None and asked != token_client


def _agent_leaves_its_client(request: ToolRequest) -> bool:
    """An agent calls a client tool without naming the client of its token."""
    if not _is_agent(request) or request.tool not in CLIENT_TOOLS:
        return False
    return not _names_own_client(request)


def _incomplete_agent_token(request: ToolRequest) -> bool:
    """An agent token that carries no client or no mode (specification 12.6)."""
    if not _is_agent(request):
        return False
    return not request.principal.client_id or request.principal.mode is None


def _unknown_tool(request: ToolRequest) -> bool:
    """The gateway does not expose this tool."""
    return request.tool not in TOOLS


def _is_author(request: ToolRequest) -> bool:
    """The caller is the engineer who did the work the call points at.

    The table 10.5 opens the deletion to the administrator, and to the engineer
    for **its own** work only - a referent does not delete (chapter 7). The tool
    resolves the author of the work item in the catalogue and passes its
    pseudonym as the ``author`` parameter; the engine compares it with the
    pseudonym of the caller, which comes from the token. The engine itself never
    reads the catalogue: it decides on the token and on the parameters.
    """
    if request.principal.role is not PrincipalRole.ENGINEER:
        return False
    author = request.arguments.get("author")
    return isinstance(author, str) and author == pseudonym(request.principal.subject)


def _is_admin(request: ToolRequest) -> bool:
    """The caller administers the MSP: sources, clients, roles, deletion."""
    return request.principal.role is PrincipalRole.ADMIN


def _is_referent(request: ToolRequest) -> bool:
    """The caller is the referent of the domain (specification 7)."""
    return request.principal.role is PrincipalRole.REFERENT


# -- Cedar fragments -------------------------------------------------------- #
def cedar_role_is(*roles: PrincipalRole) -> str:
    """A Cedar condition: the token carries one of these roles."""
    values = " || ".join(f'principal.getTag("role") == "{role.value}"' for role in roles)
    return f'principal.hasTag("role") &&\n  {values if len(roles) == 1 else f"({values})"}'


def cedar_role_is_not(*roles: PrincipalRole) -> str:
    """A Cedar condition: the token carries none of these roles."""
    values = " && ".join(f'principal.getTag("role") != "{role.value}"' for role in roles)
    return f'principal.hasTag("role") &&\n  {values}'


def cedar_agent_mode(mode: AgentMode) -> str:
    """A Cedar condition: the token is an agent in this mode."""
    return (
        'principal.hasTag("role") &&\n'
        '  principal.getTag("role") == "agent" &&\n'
        '  principal.hasTag("mode") &&\n'
        f'  principal.getTag("mode") == "{mode.value}"'
    )


def cedar_own_client(mode: AgentMode | None = None) -> str:
    """A Cedar condition: an agent, in this mode when one is given, of its client."""
    lines = ['principal.hasTag("role") &&', '  principal.getTag("role") == "agent" &&']
    if mode is not None:
        lines += ['  principal.hasTag("mode") &&', f'  principal.getTag("mode") == "{mode.value}" &&']
    lines += ['  principal.hasTag("clientId") &&', '  context.input.clientId == principal.getTag("clientId")']
    return "\n".join(lines)


CEDAR_CROSS_CLIENT = (
    'principal.hasTag("clientId") &&\n  context.input.clientId != principal.getTag("clientId")'
)
CEDAR_INCOMPLETE_AGENT = (
    'principal.hasTag("role") &&\n'
    '  principal.getTag("role") == "agent" &&\n'
    '  (!principal.hasTag("clientId") || !principal.hasTag("mode"))'
)
CEDAR_AUTHOR = (
    'principal.hasTag("role") &&\n'
    '  principal.getTag("role") == "engineer" &&\n'
    '  principal.hasTag("author") &&\n'
    '  context.input.author == principal.getTag("author")'
)
CEDAR_KNOWN_ROLE = cedar_role_is(*KNOWN_ROLES)

# -- the interdictions ------------------------------------------------------ #
#: Read in this order: the first interdiction that matches decides the refusal.
FORBIDS: tuple[Rule, ...] = (
    Rule(
        id="S.4",
        effect=Decision.FORBID,
        action="*",
        when=_unknown_tool,
        why="outil absent de la passerelle : le refus est la règle par défaut",
    ),
    Rule(
        id="S.1",
        effect=Decision.FORBID,
        action="*",
        when=_crosses_clients,
        why="le client demandé n'est pas celui du jeton : le périmètre se lit "
        "dans le jeton, jamais dans l'appel",
        cedar=CEDAR_CROSS_CLIENT,
        cedar_actions=CLIENT_TOOLS,
    ),
    Rule(
        id="S.2",
        effect=Decision.FORBID,
        action="*",
        when=_agent_leaves_its_client,
        why="un agent doit déclarer le client de son jeton, et aucun autre",
    ),
    Rule(
        id="S.3",
        effect=Decision.FORBID,
        action="*",
        when=_incomplete_agent_token,
        why="jeton d'agent incomplet : il doit porter un rôle, un mode et un client",
        cedar=CEDAR_INCOMPLETE_AGENT,
    ),
    Rule(
        id="D.2",
        effect=Decision.FORBID,
        action="recall_all",
        when=_is_autonomous,
        why="le contenu non validé est interdit aux agents autonomes",
        cedar=f'principal.hasTag("mode") &&\n  principal.getTag("mode") == "{AgentMode.AUTONOMOUS.value}"',
    ),
    Rule(
        id="M.1",
        effect=Decision.FORBID,
        action="propose_fiche",
        when=_is_autonomous,
        why="un agent autonome ne propose pas de fiche : la proposition attend une relecture humaine",
        cedar=cedar_agent_mode(AgentMode.AUTONOMOUS),
    ),
    Rule(
        id="M.2",
        effect=Decision.FORBID,
        action="validate_fiche",
        when=lambda request: not _is_referent(request),
        why="la validation d'une fiche est réservée au référent",
        cedar=cedar_role_is_not(PrincipalRole.REFERENT),
    ),
    Rule(
        id="D.4",
        effect=Decision.FORBID,
        action="publish_common",
        when=lambda request: not _is_referent(request),
        why="seul un référent publie dans le commun",
        cedar=cedar_role_is(PrincipalRole.REFERENT),
        cedar_clause="unless",
    ),
    Rule(
        id="M.3",
        effect=Decision.FORBID,
        action="set_aside",
        when=lambda request: not (_is_referent(request) or _is_admin(request)),
        why="mettre une fiche à l'écart est réservé au référent et à l'administrateur",
        cedar=cedar_role_is_not(PrincipalRole.REFERENT, PrincipalRole.ADMIN),
    ),
    Rule(
        id="M.4",
        effect=Decision.FORBID,
        action="export_skill",
        when=lambda request: not _is_referent(request),
        why="l'export en compétence est réservé au référent",
        cedar=cedar_role_is_not(PrincipalRole.REFERENT),
    ),
    Rule(
        id="M.5",
        effect=Decision.FORBID,
        action="forget",
        when=lambda request: not (_is_admin(request) or _is_author(request)),
        why="seul l'administrateur supprime, et l'auteur pour ses propres travaux",
        cedar=f"({cedar_role_is_not(PrincipalRole.ADMIN)}) &&\n  !({CEDAR_AUTHOR})",
    ),
)

# -- the authorisations ----------------------------------------------------- #
#: An authorisation never widens an interdiction: the engine checks both.
PERMITS: tuple[Rule, ...] = (
    Rule(
        id="D.1",
        effect=Decision.PERMIT,
        action="recall",
        when=lambda request: _is_agent(request) and _names_own_client(request),
        why="un agent consulte le validé de son client, et le commun",
        cedar=cedar_own_client(),
    ),
    Rule(
        id="P.1",
        effect=Decision.PERMIT,
        action="recall",
        when=lambda request: _has_role(request, READER_ROLES),
        why="un humain consulte le validé",
        cedar=cedar_role_is(*READER_ROLES),
    ),
    Rule(
        id="P.2",
        effect=Decision.PERMIT,
        action="recall_all",
        when=lambda request: _has_role(request, READER_ROLES),
        why="un humain consulte aussi le contenu non validé, signalé comme tel",
        cedar=cedar_role_is(*READER_ROLES),
    ),
    Rule(
        id="P.3",
        effect=Decision.PERMIT,
        action="recall_all",
        when=lambda request: _is_assisted(request) and _names_own_client(request),
        why="un agent assisté consulte le validé et le non validé de son client",
        cedar=cedar_own_client(AgentMode.ASSISTED),
    ),
    Rule(
        id="P.4",
        effect=Decision.PERMIT,
        action="get_item",
        when=lambda request: _has_role(request, KNOWN_ROLES),
        why="tout appelant connu lit un objet ; le périmètre de l'objet est contrôlé par l'outil",
        cedar=CEDAR_KNOWN_ROLE,
    ),
    Rule(
        id="D.5",
        effect=Decision.PERMIT,
        action="record_episode",
        when=lambda request: _is_agent(request) and _names_own_client(request),
        why="un agent déclare un épisode pour son client",
        cedar=cedar_own_client(),
    ),
    Rule(
        id="P.5",
        effect=Decision.PERMIT,
        action="record_episode",
        when=lambda request: _has_role(request, READER_ROLES),
        why="un humain déclare un épisode",
        cedar=cedar_role_is(*READER_ROLES),
    ),
    Rule(
        id="P.6",
        effect=Decision.PERMIT,
        action="report_outcome",
        when=lambda request: _has_role(request, KNOWN_ROLES),
        why="tout appelant donne un retour d'usage",
        cedar=CEDAR_KNOWN_ROLE,
    ),
    Rule(
        id="P.7",
        effect=Decision.PERMIT,
        action="propose_fiche",
        when=lambda request: _has_role(request, AUTHOR_ROLES),
        why="un humain propose une fiche",
        cedar=cedar_role_is(*AUTHOR_ROLES),
    ),
    Rule(
        id="P.8",
        effect=Decision.PERMIT,
        action="propose_fiche",
        when=_is_assisted,
        why="un agent assisté propose une fiche, relue par un humain",
        cedar=cedar_agent_mode(AgentMode.ASSISTED),
    ),
    Rule(
        id="D.3",
        effect=Decision.PERMIT,
        action="validate_fiche",
        when=_is_referent,
        why="un référent valide une fiche",
        cedar=cedar_role_is(PrincipalRole.REFERENT),
    ),
    Rule(
        id="P.9",
        effect=Decision.PERMIT,
        action="publish_common",
        when=_is_referent,
        why="un référent publie dans le commun",
        cedar=cedar_role_is(PrincipalRole.REFERENT),
    ),
    Rule(
        id="P.10",
        effect=Decision.PERMIT,
        action="set_aside",
        when=lambda request: _is_referent(request) or _is_admin(request),
        why="un référent ou l'administrateur met une fiche à l'écart",
        cedar=cedar_role_is(PrincipalRole.REFERENT, PrincipalRole.ADMIN),
    ),
    Rule(
        id="P.11",
        effect=Decision.PERMIT,
        action="export_skill",
        when=_is_referent,
        why="un référent exporte une fiche en compétence",
        cedar=cedar_role_is(PrincipalRole.REFERENT),
    ),
    Rule(
        id="P.12",
        effect=Decision.PERMIT,
        action="forget",
        when=lambda request: _is_admin(request) or _is_author(request),
        why="l'administrateur supprime, et l'auteur supprime ses propres travaux",
        cedar=f"({cedar_role_is(PrincipalRole.ADMIN)}) || ({CEDAR_AUTHOR})",
    ),
)

#: Every policy of the gateway, interdictions first: the order decides which
#: rule the journal names when several of them match.
RULES: tuple[Rule, ...] = FORBIDS + PERMITS

#: The rules of annex D, in the order of the annex.
ANNEX_D: tuple[str, ...] = ("D.1", "D.2", "D.3", "D.4", "D.5")


def rules_of(tool: str) -> tuple[Rule, ...]:
    """The rules that apply to one tool, interdictions first."""
    return tuple(rule for rule in RULES if rule.action in {"*", tool})


__all__ = [
    "ANNEX_D",
    "AUTHOR_ROLES",
    "CLIENT_TOOLS",
    "FORBIDS",
    "KNOWN_ROLES",
    "PERMITS",
    "READER_ROLES",
    "RULES",
    "TOOLS",
    "Rule",
    "rules_of",
]
