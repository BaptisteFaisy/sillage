"""The memory spaces: the templates of annex A, and the rule of chapter 10.4.

Two things are checked here, and they are the two halves of the design:

* the spaces are the ones of the specification - an episode under its client
  and its session, the lessons under their client, the approved text under its
  perimeter, the common knowledge in its own space;
* the spaces a caller may read are computed from its **token**, never from a
  text: an agent of client B never receives the space of client A, whatever it
  writes in its query (R9, R15, EF-GEN-07).
"""

from __future__ import annotations

import inspect

import pytest

from memory_fakes import client_space, connection_job, record, replay
from sillage.kernel.config import (
    COMMON_NAMESPACE,
    EPISODE_NAMESPACE,
    FICHE_NAMESPACE,
    REFLECTION_NAMESPACE,
    Settings,
)
from sillage.kernel.protocols import Principal
from sillage.kernel.types import AgentMode, PrincipalRole, Scope, ScopeKind
from sillage.memory.local import LocalMemoryBackend
from sillage.memory.namespaces import (
    common_namespace,
    episode_namespace,
    fiche_namespace,
    namespace_of_token,
    namespaces_of_scope,
    reflection_namespace,
    scope_of_actor,
)


@pytest.mark.recette
class TestTheSpacesOfAnnexA:
    """The shapes are the ones of the specification, read in the kernel templates."""

    def test_an_episode_lives_under_its_client_and_its_session(self, settings, scope_a) -> None:
        namespace = episode_namespace(settings, scope_a, "guichet:2041")
        assert namespace == EPISODE_NAMESPACE.format(
            memoryStrategyId="episodes_travail",
            actorId="client-a",
            sessionId="guichet:2041",
        )
        assert namespace.startswith("/strategy/")
        assert "/actor/client-a/session/" in namespace

    def test_the_lessons_live_under_their_client(self, settings, scope_a) -> None:
        namespace = reflection_namespace(settings, scope_a)
        assert namespace == REFLECTION_NAMESPACE.format(
            memoryStrategyId="episodes_travail", actorId="client-a"
        )
        assert namespace.endswith("/actor/client-a/")

    def test_the_approved_text_lives_under_its_perimeter(self, settings, scope_a) -> None:
        assert fiche_namespace(settings, scope_a) == FICHE_NAMESPACE.format(scope="client-a")
        assert fiche_namespace(settings, scope_a) == "/fiches/client-a/"

    def test_the_common_knowledge_lives_in_its_own_space(self, settings) -> None:
        assert common_namespace(settings) == COMMON_NAMESPACE == "/commun/"

    def test_the_common_scope_uses_the_common_space(self, settings) -> None:
        common = Scope(kind=ScopeKind.COMMON)
        assert fiche_namespace(settings, common) == "/commun/"

    def test_the_internal_perimeter_has_its_own_space(self, settings) -> None:
        interne = Scope(kind=ScopeKind.INTERNAL)
        assert fiche_namespace(settings, interne) == "/fiches/interne/"
        assert reflection_namespace(settings, interne).endswith("/actor/interne/")

    def test_the_templates_come_from_the_kernel(self, settings, scope_a) -> None:
        """One implementation per shape: the kernel owns the templates."""
        assert episode_namespace(settings, scope_a, "w-1") == settings.episode_namespace(
            scope_a.actor_id, "w-1"
        )
        assert reflection_namespace(settings, scope_a) == settings.reflection_namespace("client-a")


@pytest.mark.recette
class TestTheSpacesOfAToken:
    """Chapter 10.4: the space comes from the token, never from a text."""

    def test_an_agent_of_client_b_never_receives_the_space_of_client_a(
        self, settings, assisted_agent_b
    ) -> None:
        namespaces = namespace_of_token(settings, assisted_agent_b)
        assert namespaces
        assert not any("client-a" in namespace for namespace in namespaces)
        assert any("client-b" in namespace for namespace in namespaces)

    def test_the_common_knowledge_is_always_reachable(self, settings, assisted_agent_b) -> None:
        assert "/commun/" in namespace_of_token(settings, assisted_agent_b)

    def test_the_home_perimeter_comes_first(self, settings, assisted_agent_b) -> None:
        """The perimeter of the caller is searched before the common knowledge."""
        namespaces = namespace_of_token(settings, assisted_agent_b)
        assert namespaces[-1] == "/commun/"
        assert any("client-b" in namespace for namespace in namespaces[:-1])

    def test_nothing_of_the_query_can_widen_the_answer(self, settings, assisted_agent_b, scope_a) -> None:
        """The function takes a token and a settings: no free text at all."""
        assert list(inspect.signature(namespace_of_token).parameters) == ["settings", "principal"]
        namespaces = namespace_of_token(settings, assisted_agent_b)
        for namespace in namespaces:
            assert namespace != reflection_namespace(settings, scope_a)
            assert namespace != fiche_namespace(settings, scope_a)

    def test_the_token_is_the_only_source(self, settings, assisted_agent_b) -> None:
        """Two tokens of two clients give two different sets of spaces."""
        other = Principal(
            subject="agent-kiro-a", role=PrincipalRole.AGENT, client_id="a", mode=AgentMode.ASSISTED
        )
        mine = namespace_of_token(settings, assisted_agent_b)
        theirs = namespace_of_token(settings, other)
        assert set(mine).isdisjoint(set(theirs) - {"/commun/"})

    def test_a_human_without_client_reads_the_msp_and_the_common(self, settings, admin) -> None:
        namespaces = namespace_of_token(settings, admin)
        assert any("interne" in namespace for namespace in namespaces)
        assert "/commun/" in namespaces

    def test_a_referent_of_client_a_reads_client_a(self, settings, referent) -> None:
        namespaces = namespace_of_token(settings, referent)
        assert any("client-a" in namespace for namespace in namespaces)
        assert "/commun/" in namespaces

    def test_the_answer_is_stable(self, settings, assisted_agent_b) -> None:
        assert namespace_of_token(settings, assisted_agent_b) == namespace_of_token(
            settings, assisted_agent_b
        )


@pytest.mark.recette
class TestTheSpacesAreDisjoint:
    """R15: the spaces of two clients never overlap, by construction."""

    def test_the_spaces_of_two_clients_are_disjoint(self, settings, scope_a, scope_b) -> None:
        mine = namespaces_of_scope(settings, scope_a)
        theirs = namespaces_of_scope(settings, scope_b)
        assert not set(mine) & set(theirs)

    def test_one_space_is_never_a_prefix_of_another(self, settings, scope_a, scope_b) -> None:
        mine = namespaces_of_scope(settings, scope_a)
        theirs = namespaces_of_scope(settings, scope_b)
        for left in mine:
            for right in theirs:
                assert not right.startswith(left)
                assert not left.startswith(right)

    def test_the_common_space_is_its_own(self, settings) -> None:
        assert namespaces_of_scope(settings, Scope(kind=ScopeKind.COMMON)) == ("/commun/",)

    def test_the_actor_of_a_scope_is_its_perimeter(self, scope_a, scope_b) -> None:
        assert scope_of_actor("client-a") == scope_a
        assert scope_of_actor("client-b") == scope_b
        assert scope_of_actor("interne") == Scope(kind=ScopeKind.INTERNAL)
        assert scope_of_actor("commun") == Scope(kind=ScopeKind.COMMON)

    def test_the_round_trip_holds_for_the_three_perimeters(self) -> None:
        for scope in (
            Scope(kind=ScopeKind.CLIENT, client_id="a"),
            Scope(kind=ScopeKind.INTERNAL),
            Scope(kind=ScopeKind.COMMON),
        ):
            assert scope_of_actor(scope.actor_id).actor_id == scope.actor_id


def test_the_namespaces_of_a_client_cover_its_lessons_and_its_fiches(settings, scope_a) -> None:
    assert namespaces_of_scope(settings, scope_a) == (
        "/strategy/episodes_travail/actor/client-a/",
        "/fiches/client-a/",
    )


def test_the_settings_decide_the_strategy(settings: Settings, scope_a) -> None:
    """A deployed settings carries the strategy id: the space follows it."""
    deployed = Settings(strategy_id="strat-42")
    assert reflection_namespace(deployed, scope_a) == "/strategy/strat-42/actor/client-a/"


@pytest.mark.recette
class TestR15TheCommonSpaceNeverLeadsBack:
    """R15 / EF-GEN-07: a common lesson cannot lead back to its episode."""

    @pytest.fixture
    def shared(self, settings, clock) -> LocalMemoryBackend:
        """Three episodes of client A, then one approved lesson in the common space."""
        backend = LocalMemoryBackend(settings, clock)
        for number in range(3):
            replay(backend, connection_job(number))
        backend.put_records(
            [
                record(
                    record_id="co-1",
                    namespace="/commun/",
                    content="Rétablir la taille du groupe de connexions après un déploiement.",
                    metadata={"kind": "approved", "scope": "commun"},
                )
            ]
        )
        return backend

    def test_the_common_space_holds_no_episode_of_a_client(self, shared, settings) -> None:
        found = shared.list_records(namespace_path=settings.common_namespace())
        assert [hit.record_id for hit in found] == ["co-1"]

    def test_a_client_reading_the_common_space_reaches_no_episode(self, shared, settings) -> None:
        hits = shared.retrieve(namespace=settings.common_namespace(), query="connexions base", top_k=10)
        assert [hit.record_id for hit in hits] == ["co-1"]
        assert all("client-a" not in hit.namespace for hit in hits)

    def test_the_common_record_carries_no_episode_of_a_client(self, shared) -> None:
        common = shared.record("co-1")
        episodes = {record_.record_id for record_ in shared.list_records(namespace_path=client_space(shared))}
        assert not set(common.metadata.values()) & episodes
        assert not any(episode in common.content for episode in episodes)

    def test_the_spaces_of_the_two_clients_are_disjoint(self, shared) -> None:
        mine = {hit.namespace for hit in shared.list_records(namespace_path=client_space(shared))}
        theirs = {hit.namespace for hit in shared.list_records(namespace_path=client_space(shared, "b"))}
        assert mine and not theirs
