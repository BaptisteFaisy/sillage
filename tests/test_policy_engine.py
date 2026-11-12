"""The policy engine: default deny, forbid wins, two modes, a journal.

Every assertion quotes a line of the specification: chapter 10.1 (the three
layers, the two modes), 10.4 (the rules and the means), 10.5 (who may do
what), annex D (the five policies) and the recipes R7, R8, R9.
"""

from __future__ import annotations

import pytest

from sillage.catalog.local import ListJournal
from sillage.kernel.config import Settings
from sillage.kernel.errors import PolicyDenied
from sillage.kernel.ids import pseudonym
from sillage.kernel.protocols import Authorization, Principal, ToolRequest
from sillage.kernel.protocols import PolicyEngine as PolicyEnginePort
from sillage.kernel.types import AgentMode, Decision, PrincipalRole
from sillage.policy.engine import ENFORCE, LOG_ONLY, PolicyEngine, build_default_engine
from sillage.policy.journal import entry
from sillage.policy.principal import demo_token, from_token
from sillage.policy.rules import ANNEX_D, CLIENT_TOOLS, RULES, TOOLS, Rule

PERMIT = Decision.PERMIT.value
FORBID = Decision.FORBID.value
DENY = Decision.DEFAULT_DENY.value


def agent(
    client_id: str | None = "a",
    mode: AgentMode | None = AgentMode.AUTONOMOUS,
    subject: str = "agent-a",
) -> Principal:
    """An agent token: a role, a mode and a client (specification 12.6)."""
    return Principal(subject=subject, role=PrincipalRole.AGENT, client_id=client_id, mode=mode)


def human(
    role: PrincipalRole = PrincipalRole.ENGINEER, client_id: str | None = None, subject: str = "p-1"
) -> Principal:
    """A human token: a role, and a client only when the identity gives one."""
    return Principal(subject=subject, role=role, client_id=client_id)


def call(tool: str, principal: Principal, **arguments: object) -> ToolRequest:
    return ToolRequest(tool=tool, principal=principal, arguments=dict(arguments))


def arguments_of(tool: str, principal: Principal) -> dict[str, object]:
    """The parameters of a call, as the tool of chapter 15.1 receives them."""
    if tool in CLIENT_TOOLS:
        return {"clientId": principal.client_id or "a", "problem": "erreurs 500 après un déploiement"}
    if tool == "forget":
        return {"workItemId": "guichet:ticket-2041", "author": pseudonym(principal.subject)}
    if tool == "get_item":
        return {"id": "f-1a2b3c"}
    if tool == "report_outcome":
        return {"usageId": "u-1a2b3c", "result": "helped"}
    if tool == "propose_fiche":
        return {"episodeIds": ["ep-1a2b3c"]}
    return {"ficheId": "f-1a2b3c", "decision": "accept", "reason": "deux échecs de suite"}


@pytest.fixture
def engine(clock) -> PolicyEngine:
    """The offline engine, in ENFORCE mode, with the clock of the tests."""
    return build_default_engine(clock=clock)


# -- the callers of chapter 10.5 -------------------------------------------- #
ENGINEER = human(PrincipalRole.ENGINEER)
REFERENT = human(PrincipalRole.REFERENT, subject="p-referent")
ASSISTED = agent("a", AgentMode.ASSISTED, "agent-assisted-a")
AUTONOMOUS = agent("a", AgentMode.AUTONOMOUS, "agent-autonomous-a")
ADMIN = human(PrincipalRole.ADMIN, subject="p-admin")
TEAM_LEAD = human(PrincipalRole.TEAM_LEAD, subject="p-lead")

#: The five columns of the table 10.5, in order.
CALLERS: tuple[tuple[str, Principal], ...] = (
    ("ingenieur", ENGINEER),
    ("referent", REFERENT),
    ("agent-assiste", ASSISTED),
    ("agent-autonome", AUTONOMOUS),
    ("administrateur", ADMIN),
)

#: Table 10.5 of the specification: one row per action, the five columns of
#: ``CALLERS`` in order. "Son client" is checked by the isolation tests: here
#: the caller always asks for the client its token carries.
TABLE_105: dict[str, tuple[str, ...]] = {
    "recall": (PERMIT, PERMIT, PERMIT, PERMIT, PERMIT),
    "recall_all": (PERMIT, PERMIT, PERMIT, FORBID, PERMIT),
    "get_item": (PERMIT, PERMIT, PERMIT, PERMIT, PERMIT),
    "record_episode": (PERMIT, PERMIT, PERMIT, PERMIT, PERMIT),
    "report_outcome": (PERMIT, PERMIT, PERMIT, PERMIT, PERMIT),
    "propose_fiche": (PERMIT, PERMIT, PERMIT, FORBID, PERMIT),
    "validate_fiche": (FORBID, PERMIT, FORBID, FORBID, FORBID),
    "publish_common": (FORBID, PERMIT, FORBID, FORBID, FORBID),
    "set_aside": (FORBID, PERMIT, FORBID, FORBID, PERMIT),
    "export_skill": (FORBID, PERMIT, FORBID, FORBID, FORBID),
    # "Ses propres travaux": the call carries the pseudonym of the author.
    "forget": (PERMIT, FORBID, FORBID, FORBID, PERMIT),
}

MATRIX_105: tuple[tuple[str, str, Principal, str], ...] = tuple(
    (tool, name, principal, TABLE_105[tool][index])
    for tool in TABLE_105
    for index, (name, principal) in enumerate(CALLERS)
)


class TestDefaultDeny:
    """Chapter 10.1: "le refus est la règle par défaut"."""

    def test_an_unknown_tool_is_refused(self, engine):
        decision = engine.decide(call("recall_everything", REFERENT, clientId="a"))
        assert decision.decision is Decision.FORBID
        assert decision.rule == "S.4"

    def test_a_principal_no_rule_names_is_refused(self, engine):
        roleless = from_token({"sub": "someone", "role": "stagiaire"})
        for tool in TOOLS:
            request = ToolRequest(tool=tool, principal=roleless, arguments=arguments_of(tool, roleless))
            assert engine.decide(request).decision is not Decision.PERMIT, tool

    def test_a_token_without_any_claim_is_refused(self, engine):
        nobody = from_token({})
        request = call("recall", nobody, clientId="a")
        assert engine.decide(request).decision is Decision.FORBID
        assert request.principal.client_id is None

    def test_an_engine_without_rules_refuses_everything(self):
        bare = PolicyEngine(rules=())
        assert bare.decide(call("recall", REFERENT, clientId="a")).decision is Decision.DEFAULT_DENY
        assert bare.decide(call("recall", REFERENT, clientId="a")).rule == "default-deny"

    def test_the_default_engine_can_be_built_without_rules(self):
        assert build_default_engine(rules=()).rules == ()
        assert build_default_engine().rules == RULES

    def test_the_engine_satisfies_the_kernel_port(self, engine):
        assert isinstance(engine, PolicyEnginePort)
        assert engine.mode == ENFORCE


class TestForbidWins:
    """Chapter 10.1: "une interdiction l'emporte sur une autorisation"."""

    def test_an_interdiction_beats_an_authorisation(self):
        yes = Rule(id="oui", effect=Decision.PERMIT, action="recall", when=lambda request: True, why="oui")
        no = Rule(id="non", effect=Decision.FORBID, action="recall", when=lambda request: True, why="non")
        for rules in ((yes, no), (no, yes)):
            decision = PolicyEngine(rules=rules).decide(call("recall", ENGINEER, clientId="a"))
            assert decision.decision is Decision.FORBID
            assert decision.rule == "non"

    def test_an_incomplete_token_loses_its_authorisation(self, engine):
        """D.1 authorises the call, S.3 forbids it: the interdiction decides."""
        incomplete = agent("a", None, "agent-sans-mode")
        decision = engine.decide(call("recall", incomplete, clientId="a"))
        assert decision.decision is Decision.FORBID
        assert decision.rule == "S.3"

    def test_a_cross_client_call_loses_its_authorisation(self, engine):
        decision = engine.decide(call("record_episode", ASSISTED, clientId="b", workItemId="w-1"))
        assert decision.decision is Decision.FORBID
        assert decision.rule == "S.1"


class TestModes:
    """Chapter 10.1: two modes, ``LOG_ONLY`` and ``ENFORCE``."""

    def test_enforce_raises_and_names_the_rule(self, engine):
        with pytest.raises(PolicyDenied) as caught:
            engine.enforce(call("validate_fiche", ENGINEER, ficheId="f-1"))
        assert caught.value.rule == "M.2"
        assert caught.value.tool == "validate_fiche"
        assert caught.value.code == "policy_denied"

    def test_log_only_returns_the_refusal_without_raising(self):
        engine = build_default_engine(mode=LOG_ONLY)
        assert engine.enforce(call("validate_fiche", ENGINEER, ficheId="f-1")) is None
        assert engine.authorize(call("validate_fiche", ENGINEER, ficheId="f-1")).decision.value == FORBID

    def test_log_only_still_journals_the_refusal(self, clock):
        journal = ListJournal()
        engine = build_default_engine(mode=LOG_ONLY, journal=journal, clock=clock)
        engine.enforce(call("recall_all", AUTONOMOUS, clientId="a"))
        written = journal.query(decision=Decision.FORBID)
        assert len(written) == 1
        assert written[0].tool == "recall_all"

    def test_enforce_lets_an_allowed_call_through(self, engine):
        assert engine.enforce(call("recall", REFERENT, clientId="a")) is None

    def test_the_mode_comes_from_the_settings(self):
        assert build_default_engine(settings=Settings(policy_mode=LOG_ONLY)).mode == LOG_ONLY
        assert build_default_engine(settings=Settings(policy_mode=ENFORCE)).mode == ENFORCE

    def test_an_unknown_mode_falls_back_to_the_settings(self):
        engine = build_default_engine(settings=Settings(policy_mode=LOG_ONLY), mode="SANS_EFFET")
        assert engine.mode == LOG_ONLY

    def test_the_default_mode_is_enforce(self):
        assert build_default_engine().mode == ENFORCE


class TestJournal:
    """ENF-SEC-05, EF-ADM-02, ENF-IA-03: every decision is written."""

    def test_every_decision_is_journalised(self, clock):
        journal = ListJournal()
        engine = build_default_engine(journal=journal, clock=clock)
        engine.authorize(call("recall", REFERENT, clientId="a"))
        engine.authorize(call("recall", AUTONOMOUS, clientId="b"))
        assert len(journal) == 2
        assert {written.decision for written in journal.entries()} == {Decision.PERMIT, Decision.FORBID}

    def test_a_refusal_names_the_rule_and_why(self, clock):
        journal = ListJournal()
        engine = build_default_engine(journal=journal, clock=clock)
        engine.authorize(call("validate_fiche", ENGINEER, ficheId="f-1"))
        written = journal.entries()[0]
        assert written.decision is Decision.FORBID
        assert written.reason.startswith("M.2 — ")
        assert "référent" in written.reason

    def test_the_entry_carries_caller_tool_decision_and_scope(self, clock):
        journal = ListJournal()
        engine = build_default_engine(journal=journal, clock=clock)
        engine.authorize(call("recall", AUTONOMOUS, clientId="b"))
        written = journal.entries()[0]
        assert written.caller == pseudonym("agent-autonomous-a")
        assert written.caller_role is PrincipalRole.AGENT
        assert written.tool == "recall"
        assert written.scope == "client-b"
        assert written.at == clock.now()
        assert written.entry_id.startswith("j-")

    def test_the_caller_is_a_pseudonym_never_an_identity(self, clock):
        journal = ListJournal()
        engine = build_default_engine(journal=journal, clock=clock)
        request = call("recall", human(PrincipalRole.ENGINEER, subject="prenom.nom@msp.fr"), clientId="a")
        engine.authorize(request)
        written = journal.entries()[0]
        assert "prenom.nom" not in written.caller
        assert written.caller == pseudonym("prenom.nom@msp.fr")

    def test_the_scope_of_a_call_without_a_client_is_the_one_of_the_token(self, clock):
        written = entry(
            request=call("validate_fiche", REFERENT, ficheId="f-1"),
            authorization=Authorization(decision=Decision.PERMIT, reason="oui", rule="D.3"),
            clock=clock,
        )
        assert written.scope == "interne"

    def test_the_journal_of_a_call_carries_its_usage_and_work_item(self, clock):
        written = entry(
            request=call("recall", ENGINEER, clientId="a"),
            authorization=Authorization(decision=Decision.PERMIT, reason="oui", rule="P.1"),
            usage_id="u-1",
            work_item_id="guichet:ticket-2041",
            clock=clock,
        )
        assert (written.usage_id, written.work_item_id) == ("u-1", "guichet:ticket-2041")

    def test_the_journal_is_not_written_when_the_settings_say_so(self, clock):
        journal = ListJournal()
        engine = build_default_engine(
            settings=Settings(journal_decisions=False), journal=journal, clock=clock
        )
        engine.authorize(call("recall", ENGINEER, clientId="a"))
        assert len(journal) == 0


class TestAnnexD:
    """The five policies of annex D, each in both directions."""

    def test_d1_an_agent_reads_its_own_client(self, engine):
        assert engine.decide(call("recall", AUTONOMOUS, clientId="a")).decision.value == PERMIT

    def test_d1_an_agent_does_not_read_another_client(self, engine):
        decision = engine.decide(call("recall", AUTONOMOUS, clientId="b"))
        assert decision.decision.value == FORBID
        assert decision.rule == "S.1"

    def test_d2_an_autonomous_agent_gets_no_unvalidated_content(self, engine):
        decision = engine.decide(call("recall_all", AUTONOMOUS, clientId="a"))
        assert decision.decision.value == FORBID
        assert decision.rule == "D.2"

    def test_d2_an_assisted_agent_reads_its_own_client(self, engine):
        assert engine.decide(call("recall_all", ASSISTED, clientId="a")).decision.value == PERMIT

    def test_d3_a_referent_validates(self, engine):
        assert engine.decide(call("validate_fiche", REFERENT, ficheId="f-1")).decision.value == PERMIT

    def test_d3_nobody_else_validates(self, engine):
        for principal in (ENGINEER, ASSISTED, AUTONOMOUS, ADMIN):
            assert engine.decide(call("validate_fiche", principal, ficheId="f-1")).decision.value == FORBID

    def test_d4_a_referent_publishes_in_the_common(self, engine):
        assert engine.decide(call("publish_common", REFERENT, ficheId="f-1")).decision.value == PERMIT

    def test_d4_nobody_else_publishes_in_the_common(self, engine):
        for principal in (ENGINEER, ASSISTED, AUTONOMOUS, ADMIN):
            decision = engine.decide(call("publish_common", principal, ficheId="f-1"))
            assert decision.decision.value == FORBID
            assert decision.rule == "D.4"

    def test_d5_an_agent_declares_an_episode_for_its_client(self, engine):
        request = call("record_episode", AUTONOMOUS, clientId="a", workItemId="guichet:1")
        assert engine.decide(request).decision.value == PERMIT

    def test_d5_an_agent_declares_no_episode_for_another_client(self, engine):
        request = call("record_episode", AUTONOMOUS, clientId="b", workItemId="guichet:1")
        assert engine.decide(request).decision.value == FORBID

    def test_the_five_rules_of_the_annex_are_in_the_table(self):
        assert {rule.id for rule in RULES} >= set(ANNEX_D)
        assert ANNEX_D == ("D.1", "D.2", "D.3", "D.4", "D.5")


class TestTable105:
    """Chapter 10.5, "qui peut faire quoi": the whole grid, both directions."""

    @pytest.mark.parametrize(
        ("tool", "column", "principal", "expected"),
        MATRIX_105,
        ids=[f"{tool}-{column}" for tool, column, _, _ in MATRIX_105],
    )
    def test_the_grid(self, engine, tool, column, principal, expected):
        request = ToolRequest(tool=tool, principal=principal, arguments=arguments_of(tool, principal))
        assert engine.decide(request).decision.value == expected

    def test_every_line_of_the_table_is_covered(self):
        assert set(TABLE_105) == set(TOOLS)
        assert len(MATRIX_105) == len(TOOLS) * len(CALLERS)

    def test_the_team_lead_reads_and_writes_nothing(self, engine):
        """No column in the table 10.5: the team lead keeps the read-only tools."""
        assert engine.decide(call("recall", TEAM_LEAD, clientId="a")).decision.value == PERMIT
        assert engine.decide(call("recall_all", TEAM_LEAD, clientId="a")).decision.value == PERMIT
        assert engine.decide(call("propose_fiche", TEAM_LEAD)).decision.value == DENY
        assert engine.decide(call("validate_fiche", TEAM_LEAD, ficheId="f-1")).decision.value == FORBID

    def test_only_the_administrator_forgets(self, engine):
        someone_else = call("forget", ENGINEER, workItemId="guichet:1", author=pseudonym("autre"))
        assert engine.decide(someone_else).decision.value == FORBID
        assert engine.decide(someone_else).rule == "M.5"
        own = call("forget", ENGINEER, workItemId="guichet:1", author=pseudonym(ENGINEER.subject))
        assert engine.decide(own).decision.value == PERMIT
        assert engine.decide(call("forget", ADMIN, workItemId="guichet:1")).decision.value == PERMIT

    def test_a_forged_author_does_not_open_the_deletion(self, engine):
        forged = call("forget", AUTONOMOUS, workItemId="guichet:1", author=pseudonym(ENGINEER.subject))
        assert engine.decide(forged).decision.value == FORBID


@pytest.mark.recette
class TestRecipes:
    """R7, R8 and R9 of chapter 23, the three refusals the judges watch."""

    def test_r7_an_engineer_cannot_validate(self, engine):
        decision = engine.decide(call("validate_fiche", ENGINEER, ficheId="f-1"))
        assert decision.decision.value == FORBID
        assert "référent" in decision.reason

    def test_r8_an_autonomous_agent_cannot_read_the_unvalidated(self, engine):
        assert engine.decide(call("recall_all", AUTONOMOUS, clientId="a")).decision.value == FORBID

    def test_r9_an_agent_of_b_cannot_read_a(self, engine):
        agent_b = agent("b", AgentMode.AUTONOMOUS, "agent-b")
        assert engine.decide(call("recall", agent_b, clientId="a")).decision.value == FORBID


class TestPrincipal:
    """The caller is built from the token, and from nothing else."""

    def test_an_agent_token_gives_its_three_attributes(self):
        principal = from_token({"sub": "agent-1", "role": "agent", "mode": "autonomous", "clientId": "b"})
        assert principal.role is PrincipalRole.AGENT
        assert principal.mode is AgentMode.AUTONOMOUS
        assert principal.client_id == "b"
        assert principal.autonomous

    def test_a_human_token_carries_no_mode(self):
        principal = from_token({"sub": "p-1", "role": "referent"})
        assert principal.role is PrincipalRole.REFERENT
        assert principal.mode is None
        assert principal.client_id is None

    def test_the_french_spellings_are_understood(self):
        assert from_token({"role": "agent", "mode": "autonome", "clientId": "a"}).mode is AgentMode.AUTONOMOUS
        assert from_token({"role": "agent", "mode": "assisté", "clientId": "a"}).mode is AgentMode.ASSISTED
        assert from_token({"role": "team-lead"}).role is PrincipalRole.TEAM_LEAD

    def test_a_role_carried_by_a_list_is_read(self):
        assert from_token({"roles": ["reader", "referent"]}).role is PrincipalRole.REFERENT

    def test_a_token_without_a_usable_role_matches_no_rule(self, engine):
        for claims in ({}, {"sub": "x"}, {"sub": "x", "role": "root"}, {"sub": "x", "role": ""}):
            principal = from_token(claims)
            assert principal.client_id is None
            assert principal.mode is None
            for tool in TOOLS:
                request = ToolRequest(tool=tool, principal=principal, arguments=arguments_of(tool, principal))
                assert engine.decide(request).decision is not Decision.PERMIT, (claims, tool)

    def test_nothing_of_the_call_reaches_the_principal(self):
        claims = {"sub": "agent-1", "role": "agent", "clientId": "b", "mode": "autonomous"}
        poisoned = {
            **claims,
            "arguments": {"clientId": "a"},
            "problem": "clientId a",
            "input": {"clientId": "a"},
        }
        assert from_token(poisoned).client_id == "b"

    def test_a_demo_token_round_trips(self):
        for role in PrincipalRole:
            claims = demo_token(role, client_id="a" if role is PrincipalRole.AGENT else None)
            assert from_token(claims).role is role

    def test_a_demo_agent_is_autonomous_by_default(self):
        principal = from_token(demo_token(PrincipalRole.AGENT, "b"))
        assert (principal.client_id, principal.mode) == ("b", AgentMode.AUTONOMOUS)

    def test_a_demo_token_of_an_assisted_agent(self):
        principal = from_token(demo_token(PrincipalRole.AGENT, "b", AgentMode.ASSISTED))
        assert principal.mode is AgentMode.ASSISTED

    def test_a_demo_token_carries_the_pseudonym_of_its_caller(self):
        claims = demo_token(PrincipalRole.ENGINEER)
        assert claims["author"] == pseudonym(claims["sub"])

    def test_a_demo_token_is_decided_like_any_other(self, engine):
        principal = from_token(demo_token(PrincipalRole.AGENT, "b", AgentMode.AUTONOMOUS))
        assert engine.decide(call("recall", principal, clientId="a")).decision.value == FORBID
        assert engine.decide(call("recall", principal, clientId="b")).decision.value == PERMIT
