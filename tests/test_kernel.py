"""Kernel tests: the vocabulary every other module relies on."""

from __future__ import annotations

import pytest

from sillage.kernel.config import Settings
from sillage.kernel.errors import LeakDetected, PolicyDenied
from sillage.kernel.ids import (
    episode_id,
    fiche_id,
    lesson_id,
    make_id,
    pseudonym,
    slug,
    trace_fingerprint,
    usage_id,
)
from sillage.kernel.protocols import Principal, ToolRequest
from sillage.kernel.types import (
    LEVEL_CODE,
    SERVED_TO_AUTONOMOUS_AGENT,
    AgentMode,
    AuthorKind,
    Claim,
    Decision,
    Level,
    PrincipalRole,
    Role,
    Scope,
    ScopeKind,
    Trace,
    TraceEvent,
)


class TestScope:
    def test_client_actor_is_the_perimeter(self):
        assert Scope(kind=ScopeKind.CLIENT, client_id="a").actor_id == "client-a"

    def test_internal_and_common_actors(self):
        assert Scope(kind=ScopeKind.INTERNAL).actor_id == "interne"
        assert Scope(kind=ScopeKind.COMMON).actor_id == "commun"
        assert Scope(kind=ScopeKind.COMMON).is_common

    def test_a_client_scope_needs_an_id(self):
        with pytest.raises(ValueError):
            Scope(kind=ScopeKind.CLIENT)

    def test_common_carries_no_client(self):
        with pytest.raises(ValueError):
            Scope(kind=ScopeKind.COMMON, client_id="a")


class TestIds:
    def test_ids_are_deterministic(self):
        assert episode_id("client-a", "guichet:2041") == episode_id("client-a", "guichet:2041")
        assert trace_fingerprint("w", "payload") == trace_fingerprint("w", "payload")

    def test_ids_are_scoped(self):
        assert episode_id("client-a", "x") != episode_id("client-b", "x")

    def test_lesson_id_ignores_episode_order(self):
        assert lesson_id("client-a", "ep-1", "ep-2") == lesson_id("client-a", "ep-2", "ep-1")

    def test_prefixes(self):
        assert make_id("fiche", "x").startswith("f-")
        assert fiche_id("client-a", "Titre").startswith("f-")
        assert usage_id("p-1", "2026-11-11", "pb").startswith("u-")

    def test_slug_is_skill_safe(self):
        assert slug("Connexions à la base épuisées après un déploiement") == (
            "connexions-a-la-base-epuisees-apres-un-deploiement"
        )
        assert slug("") == "sans-titre"

    def test_pseudonym_hides_the_identity(self):
        alias = pseudonym("jeanne.dupont@msp.example")
        assert alias.startswith("p-") and "jeanne" not in alias
        assert pseudonym("jeanne.dupont@msp.example") == alias
        assert pseudonym("someone.else@msp.example") != alias


class TestTrace:
    def test_metadata_stays_under_the_agentcore_limit(self, trace_ticket: Trace):
        settings = Settings()
        assert len(trace_ticket.metadata) <= settings.max_event_metadata

    def test_metadata_carries_scope_source_and_pseudonym(self, trace_ticket: Trace):
        meta = trace_ticket.metadata
        assert meta["scope"] == "client-a"
        assert meta["auteurType"] == AuthorKind.HUMAN.value
        assert meta["auteur"] == "p-7f3a"
        assert meta["doNotDistill"] == "0"

    def test_session_is_the_work_item(self, trace_ticket: Trace):
        assert trace_ticket.session_id == "guichet:ticket-2041"

    def test_events_keep_the_four_roles(self, trace_ticket: Trace):
        roles = {event.role for event in trace_ticket.events}
        assert roles == {Role.USER, Role.ASSISTANT, Role.TOOL, Role.OTHER}


class TestLevels:
    def test_every_level_has_a_display_code(self):
        assert set(Level) == set(LEVEL_CODE)
        assert LEVEL_CODE[Level.RAW] == "0" and LEVEL_CODE[Level.WITHDRAWN] == "R"

    def test_autonomous_agents_only_read_validated_content(self):
        assert {Level.VALIDATED, Level.PROVEN} == SERVED_TO_AUTONOMOUS_AGENT
        assert Level.PROPOSED not in SERVED_TO_AUTONOMOUS_AGENT
        assert Level.SET_ASIDE not in SERVED_TO_AUTONOMOUS_AGENT


class TestClaims:
    def test_a_claim_without_source_is_not_sourced(self):
        assert Claim(text="affirmation", episode_ids=()).sourced is False
        assert Claim(text="affirmation", episode_ids=("ep-1",)).sourced is True


class TestPrincipal:
    def test_agent_is_confined_to_its_client_plus_common(self):
        agent = Principal(subject="a", role=PrincipalRole.AGENT, client_id="b", mode=AgentMode.AUTONOMOUS)
        scopes = [scope.actor_id for scope in agent.allowed_scopes()]
        assert scopes == ["client-b", "commun"]
        assert agent.autonomous

    def test_assisted_agent_is_not_autonomous(self):
        agent = Principal(subject="a", role=PrincipalRole.AGENT, client_id="b", mode=AgentMode.ASSISTED)
        assert agent.autonomous is False and agent.is_agent

    def test_human_without_client_sees_internal_and_common(self):
        human = Principal(subject="h", role=PrincipalRole.REFERENT)
        assert [scope.actor_id for scope in human.allowed_scopes()] == ["interne", "commun"]

    def test_request_exposes_the_requested_client(self):
        request = ToolRequest(
            tool="recall", principal=Principal("a", PrincipalRole.AGENT, "b"), arguments={"clientId": "a"}
        )
        assert request.client_id == "a"


class TestSettings:
    def test_namespaces_follow_annex_a(self):
        settings = Settings(strategy_id="st-1")
        assert settings.episode_namespace("client-a", "ticket-2041") == (
            "/strategy/st-1/actor/client-a/session/ticket-2041/"
        )
        assert settings.reflection_namespace("client-a") == "/strategy/st-1/actor/client-a/"
        assert settings.fiche_namespace("client-a") == "/fiches/client-a/"
        assert settings.common_namespace() == "/commun/"

    def test_action_names_follow_annex_d(self):
        assert Settings().action_name("recall") == "Savoir___recall"
        assert Settings().action_name("publish_common") == "Savoir___publish_common"

    def test_thresholds_are_the_ones_of_the_specification(self):
        settings = Settings()
        assert (settings.proven_successes, settings.proven_distinct_authors) == (3, 2)
        assert settings.set_aside_consecutive_failures == 2
        assert settings.stale_days == 180
        assert (settings.max_fiches, settings.max_episodes) == (3, 2)
        assert settings.max_recall_tokens == 1500
        assert settings.region == "eu-central-1"

    def test_environment_overrides(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SILLAGE_MAX_FICHES", "2")
        monkeypatch.setenv("SILLAGE_OFFLINE", "false")
        settings = Settings.from_env()
        assert settings.max_fiches == 2 and settings.offline is False

    def test_a_broken_override_falls_back(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SILLAGE_MAX_FICHES", "beaucoup")
        assert Settings.from_env().max_fiches == Settings().max_fiches


class TestErrors:
    def test_a_refusal_is_serialisable_for_the_journal(self):
        error = PolicyDenied("agent autonome", rule="D.2", tool="recall_all")
        assert error.as_dict() == {
            "error": "policy_denied",
            "message": "agent autonome",
            "rule": "D.2",
            "tool": "recall_all",
        }

    def test_a_leak_carries_the_identifiers(self):
        error = LeakDetected("identifiant client", identifiers=("db-a1.clienta.internal",))
        assert error.code == "leak_detected" and error.identifiers

    def test_decision_values_are_stable(self):
        assert Decision.PERMIT.value == "permit" and Decision.DEFAULT_DENY.value == "default_deny"


class TestEventShape:
    def test_an_event_defaults_to_a_human_action(self):
        event = TraceEvent(role=Role.ASSISTANT, text="je redémarre")
        assert event.kind == "action" and event.author_kind == AuthorKind.HUMAN
