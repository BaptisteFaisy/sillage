"""Layer 1 of Sillage: the policies, decided before any tool runs.

The argument of the whole project lives here. A memory shared by humans, by
agents and by several clients is acceptable only if its limits are **provable**:
chapter 10.1 puts the decision outside the code of the agents, at the gateway,
and chapter 10.4 lists what that decision enforces - the isolation by client,
the trust levels, the roles.

The module is small on purpose:

* :mod:`sillage.policy.rules` - the policies as data, one record per rule;
* :mod:`sillage.policy.engine` - the decision: default deny, forbid wins,
  ``LOG_ONLY`` or ``ENFORCE``, every verdict journalised;
* :mod:`sillage.policy.principal` - the caller, built from the claims of the
  token and from nothing else;
* :mod:`sillage.policy.cedar` - the same table rendered in the Cedar syntax of
  AgentCore Policy, for the deployed gateway;
* :mod:`sillage.policy.journal` - the entry written for each decision.

Nothing here imports boto3, calls a model or touches the network: the offline
engine and the deployed one read the same rules.
"""

from __future__ import annotations

from sillage.policy.cedar import (
    actions_of,
    forbidden_actions,
    gateway_arn,
    policy_document,
    render_policies,
    rendered_rules,
)
from sillage.policy.engine import (
    DEFAULT_DENY_REASON,
    DEFAULT_DENY_RULE,
    ENFORCE,
    LOG_ONLY,
    MODES,
    PolicyEngine,
    build_default_engine,
)
from sillage.policy.journal import caller_of, entry, scope_of
from sillage.policy.principal import client_of, demo_token, from_token, mode_of, role_of, subject_of
from sillage.policy.rules import (
    ANNEX_D,
    AUTHOR_ROLES,
    CLIENT_TOOLS,
    FORBIDS,
    KNOWN_ROLES,
    PERMITS,
    READER_ROLES,
    RULES,
    TOOLS,
    Rule,
    rules_of,
)

__all__ = [
    "ANNEX_D",
    "AUTHOR_ROLES",
    "CLIENT_TOOLS",
    "DEFAULT_DENY_REASON",
    "DEFAULT_DENY_RULE",
    "ENFORCE",
    "FORBIDS",
    "KNOWN_ROLES",
    "LOG_ONLY",
    "MODES",
    "PERMITS",
    "READER_ROLES",
    "RULES",
    "TOOLS",
    "PolicyEngine",
    "Rule",
    "actions_of",
    "build_default_engine",
    "caller_of",
    "client_of",
    "demo_token",
    "entry",
    "forbidden_actions",
    "from_token",
    "gateway_arn",
    "mode_of",
    "policy_document",
    "render_policies",
    "rendered_rules",
    "role_of",
    "rules_of",
    "scope_of",
    "subject_of",
]
