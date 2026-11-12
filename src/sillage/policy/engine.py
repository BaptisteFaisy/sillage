"""Layer 1 of the guard-fou: the decision, taken before the tool runs.

Chapter 10.1 of the specification makes this module the proof of the project:
the isolation between clients is held by **rules**, not by the goodwill of a
model. The gateway evaluates every call here, on the principal built from the
token and on the parameters of the call, and refuses before any tool logic
exists.

Three properties of AgentCore Policy are implemented as they are documented:

* the refusal is the **default**: a call no rule authorises is refused;
* an **interdiction wins** over an authorisation, whatever the order of the
  rules;
* two modes, ``LOG_ONLY`` (the refusal is journalised and returned, it does not
  raise) and ``ENFORCE`` (it raises :class:`PolicyDenied`).

Every decision, allowed or refused, is written in the journal (ENF-SEC-05,
EF-ADM-02) so a refusal is visible after the fact (ENF-IA-03, R9).
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence

from sillage.catalog.local import ListJournal
from sillage.kernel.config import DEFAULT, Settings
from sillage.kernel.errors import PolicyDenied
from sillage.kernel.protocols import Authorization, Clock, Journal, ToolRequest
from sillage.kernel.types import Decision
from sillage.policy.journal import entry
from sillage.policy.rules import RULES, Rule

#: The two modes of AgentCore Policy (specification 10.1).
LOG_ONLY = "LOG_ONLY"
ENFORCE = "ENFORCE"
MODES: tuple[str, ...] = (LOG_ONLY, ENFORCE)

#: The verdict of a call no rule authorises: the refusal is the default.
DEFAULT_DENY_RULE = "default-deny"
DEFAULT_DENY_REASON = "aucune politique n'autorise cet appel : le refus est la règle par défaut"


class PolicyEngine:
    """Decides on a tool call, and says why.

    The engine holds no state but its journal: it reads the rules of
    :mod:`sillage.policy.rules`, never a backend, never a model, never the
    network. Two calls with the same principal and the same parameters always
    get the same decision.
    """

    def __init__(
        self,
        settings: Settings = DEFAULT,
        rules: Sequence[Rule] = RULES,
        journal: Journal | None = None,
        mode: str = ENFORCE,
        clock: Clock | None = None,
    ) -> None:
        self.settings = settings
        self.rules: tuple[Rule, ...] = tuple(rules)
        self.journal: Journal = journal if journal is not None else ListJournal()
        self.mode = mode if mode in MODES else settings.policy_mode
        self.clock = clock

    # -- the decision ------------------------------------------------------- #
    def authorize(self, request: ToolRequest) -> Authorization:
        """The decision on a call, journalised whatever it is."""
        authorization = self.decide(request)
        self._journalise(request, authorization)
        return authorization

    def decide(self, request: ToolRequest) -> Authorization:
        """Evaluate the rules without writing anything: the pure decision."""
        forbids, permits = self._matching(request)
        if forbids:
            rule = forbids[0]
            return Authorization(decision=Decision.FORBID, reason=rule.why, rule=rule.id)
        if permits:
            rule = permits[0]
            return Authorization(decision=Decision.PERMIT, reason=rule.why, rule=rule.id)
        return Authorization(
            decision=Decision.DEFAULT_DENY, reason=DEFAULT_DENY_REASON, rule=DEFAULT_DENY_RULE
        )

    def enforce(self, request: ToolRequest) -> None:
        """Refuse the call by raising, in ``ENFORCE`` mode only.

        In ``LOG_ONLY`` mode the decision is still taken, still journalised and
        still returned by :meth:`authorize`, but the call goes on: that is how
        the policies are tuned on a live gateway before they are enforced.
        """
        authorization = self.authorize(request)
        if authorization.decision is Decision.PERMIT or self.mode != ENFORCE:
            return
        raise PolicyDenied(
            f"{request.tool} refusé par la politique {authorization.rule} : {authorization.reason}",
            rule=authorization.rule,
            tool=request.tool,
            decision=authorization.decision.value,
        )

    def _matching(self, request: ToolRequest) -> tuple[list[Rule], list[Rule]]:
        """The rules that apply to a call, in the order of the table."""
        forbids: list[Rule] = []
        permits: list[Rule] = []
        for rule in self.rules:
            if not self._applies(rule, request) or not rule.when(request):
                continue
            if rule.effect is Decision.FORBID:
                forbids.append(rule)
            elif rule.effect is Decision.PERMIT:
                permits.append(rule)
        return forbids, permits

    @staticmethod
    def _applies(rule: Rule, request: ToolRequest) -> bool:
        """A rule applies to its own action, or to every action (``*``)."""
        return rule.action == "*" or rule.action == request.tool

    # -- the journal -------------------------------------------------------- #
    def _journalise(self, request: ToolRequest, authorization: Authorization) -> None:
        """Write the decision: the journal is append-only (ENF-SEC-05)."""
        if not self.settings.journal_decisions:
            return
        with contextlib.suppress(Exception):
            # A journal that cannot write must not break a call (ENF-DISP-02):
            # the decision is still returned to the gateway.
            self.journal.append(entry(request=request, authorization=authorization, clock=self.clock))


def build_default_engine(**overrides: object) -> PolicyEngine:
    """The offline engine: the rules of the specification, an append-only journal.

    Accepted overrides: ``settings``, ``rules``, ``journal``, ``mode``,
    ``clock``. Without a mode the one of the settings decides, ``ENFORCE`` by
    default - ``LOG_ONLY`` is for tuning a live gateway, not for the tests.
    """
    settings: Settings = overrides.get("settings")
    settings = DEFAULT if settings is None else settings
    rules = overrides.get("rules")
    journal = overrides.get("journal")
    mode = overrides.get("mode")
    clock = overrides.get("clock")
    return PolicyEngine(
        settings=settings,
        rules=RULES if rules is None else rules,
        journal=journal,
        mode=settings.policy_mode if mode is None else mode,
        clock=clock,
    )


__all__ = [
    "DEFAULT_DENY_REASON",
    "DEFAULT_DENY_RULE",
    "ENFORCE",
    "LOG_ONLY",
    "MODES",
    "PolicyEngine",
    "build_default_engine",
]
