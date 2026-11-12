"""The Cedar text of the policies, for the real AgentCore Policy engine.

Annex D of the specification gives five policies in the Cedar syntax; chapter
10.4 and the table 10.5 give the others. This module renders **the whole set**
from the same data the offline engine evaluates (:mod:`sillage.policy.rules`),
so the policies deployed on the gateway and the rules the tests prove cannot
drift apart.

The shape of a policy is the one of the annex, with the action names built by
``Settings.action_name(tool)`` (``Savoir___recall`` and its friends, chapter
15.1). No client identifier is written anywhere: a policy compares the
parameter of the call with an attribute of the token, it never names a client.

Two readings worth knowing before deploying:

* the mode of an agent is compared with the value the kernel carries
  (``autonomous``, ``assisted``) - the prose of the annex writes ``autonome``,
  and a policy comparing a French word with an English token would never match;
* a rule whose ``cedar`` is empty has no deployed twin: the gateway only
  exposes the tools of the specification, and a missing parameter leaves every
  policy inapplicable, so the default deny refuses.
"""

from __future__ import annotations

from typing import Any

from sillage.kernel.config import DEFAULT, Settings
from sillage.kernel.types import Decision
from sillage.policy.rules import ANNEX_D, CLIENT_TOOLS, RULES, TOOLS, Rule

#: Resource of every policy until the gateway is deployed (annex D).
GATEWAY_PLACEHOLDER = "<arn-de-la-passerelle>"

#: What the header of the rendered text says to a reader.
HEADER = """// Sillage — politiques de la passerelle « Savoir » (cahier des charges, annexe D).
// Rendues depuis la table de sillage.policy.rules : le texte déployé et les règles
// testées sont les mêmes données.
// Le refus est la règle par défaut ; une interdiction l'emporte sur une autorisation.
// Deux modes : LOG_ONLY pour régler, ENFORCE pour tenir (chapitre 10.1).
// Aucun identifiant de client n'est écrit ici : une politique compare le paramètre
// de l'appel avec un attribut du jeton."""


def gateway_arn(settings: Settings = DEFAULT) -> str:
    """The resource every policy points at: the gateway of the account."""
    if not settings.account_id:
        return GATEWAY_PLACEHOLDER
    return (
        f"arn:aws:bedrock-agentcore:{settings.region}:{settings.account_id}:gateway/{settings.gateway_name}"
    )


def render_policy(rule: Rule, action: str, settings: Settings = DEFAULT) -> str:
    """One policy, in the shape of annex D."""
    scope = (
        f"{rule.effect.value} (principal, "
        f'action == AgentCore::Action::"{settings.action_name(action)}", '
        f'resource == AgentCore::Gateway::"{gateway_arn(settings)}")'
    )
    return "\n".join(
        (
            f"// {rule.id} — {rule.why}",
            scope,
            f"{rule.cedar_clause} {{",
            f"  {_indent(rule.cedar)}",
            "};",
        )
    )


def _indent(condition: str) -> str:
    """Keep a multi-line condition readable inside the ``when`` block."""
    lines = [line.strip() for line in condition.splitlines() if line.strip()]
    return "\n  ".join(lines)


def rendered_rules() -> tuple[Rule, ...]:
    """The rules that have a deployed twin, interdictions first."""
    return tuple(rule for rule in RULES if rule.cedar)


def cedar_actions_of(rule: Rule) -> tuple[str, ...]:
    """The tools one rule is deployed on.

    A rule of every action (``*``) is rendered once per tool of the gateway:
    Cedar names an action, it does not know the wildcard of the offline table.
    """
    if rule.cedar_actions:
        return rule.cedar_actions
    return TOOLS if rule.action == "*" else (rule.action,)


def actions_of(rule: Rule, settings: Settings = DEFAULT) -> tuple[str, ...]:
    """The gateway actions one rule is deployed on."""
    return tuple(settings.action_name(action) for action in cedar_actions_of(rule))


def render_policies(settings: Settings = DEFAULT) -> str:
    """The whole Cedar policy set of the gateway, as one text."""
    blocks = [HEADER]
    for rule in rendered_rules():
        for action in cedar_actions_of(rule):
            blocks.append(render_policy(rule, action, settings))
    blocks.append(_annex_note(settings))
    return "\n\n".join(blocks) + "\n"


def _annex_note(settings: Settings = DEFAULT) -> str:
    """Where the five policies of the annex are, for a reader of the text."""
    names = ", ".join(ANNEX_D)
    tools = ", ".join(settings.action_name(tool) for tool in CLIENT_TOOLS)
    return (
        f"// Annexes D du cahier des charges : {names}.\n"
        f"// Cloisonnement par client, rendu pour chaque outil qui porte un clientId : {tools}."
    )


def policy_document(settings: Settings = DEFAULT) -> dict[str, Any]:
    """What ``infra/cdk`` feeds to AgentCore Policy.

    The Cedar text is the artefact the service validates; the list beside it
    lets a stack assert that every rule of the specification is deployed, and
    the portal show why a call was refused.
    """
    return {
        "description": "Sillage — cloisonnement par client et niveaux de confiance (annexe D)",
        "gateway": gateway_arn(settings),
        "mode": settings.policy_mode,
        "actions": [settings.action_name(tool) for tool in TOOLS],
        "cedar": render_policies(settings),
        "policies": [
            {
                "id": rule.id,
                "effect": rule.effect.value,
                "actions": list(actions_of(rule, settings)),
                "clause": rule.cedar_clause,
                "why": rule.why,
                "cedar": rule.cedar,
            }
            for rule in rendered_rules()
        ],
    }


def forbidden_actions(settings: Settings = DEFAULT) -> tuple[str, ...]:
    """The actions that carry an interdiction of their own: a summary for a screen.

    The generic interdictions (an unknown tool, an incomplete token) are left
    out: they say nothing about one tool in particular.
    """
    found = {
        action
        for rule in rendered_rules()
        if rule.effect is Decision.FORBID and (rule.action != "*" or rule.cedar_actions)
        for action in actions_of(rule, settings)
    }
    return tuple(sorted(found))


__all__ = [
    "GATEWAY_PLACEHOLDER",
    "HEADER",
    "actions_of",
    "cedar_actions_of",
    "forbidden_actions",
    "gateway_arn",
    "policy_document",
    "render_policies",
    "render_policy",
    "rendered_rules",
]
