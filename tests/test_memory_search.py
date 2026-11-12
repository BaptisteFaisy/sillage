"""The search: the reformulation into an intention, and the ranking of 13.4.

Two requirements carry this module:

* **EF-RES-09** - the problem is reformulated into an intention *before* the
  memory is queried, because episodes are indexed on their intention and lessons
  on their use case. A raw error message finds almost nothing: the test below
  proves the reformulation happens, with a model and without one;
* **EF-RES-04** - the ranking puts the perimeter of the caller before the common
  knowledge, a proven fiche before a validated one, then the success rate and the
  freshness. Every one of those figures comes from the catalogue, computed by
  code (ENF-IA-01), never from a model.

The isolation is checked too: a search for client B never returns a record of
client A, even when the query names client A - the spaces come from the
perimeter, not from the text (specification 10.4, R9, R15).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from memory_fakes import MOMENT, ScriptedModel, client_space, connection_job, fiche_of, record, replay
from sillage.catalog.local import LocalCatalog
from sillage.catalog.scoring import success_rate
from sillage.kernel.types import (
    SERVED_TO_AUTONOMOUS_AGENT,
    SERVED_TO_HUMAN,
    Level,
    Scope,
    ScopeKind,
)
from sillage.memory.local import LocalMemoryBackend
from sillage.memory.namespaces import fiche_namespace, reflection_namespace
from sillage.memory.search import (
    LEVEL_ORDER,
    SearchHit,
    hit_of,
    query_namespaces,
    rank,
    rewrite_intent,
    scope_of_namespace,
    search,
)

#: The approved text of a fiche: an intention, not an error message.
FICHE_TEXT = "Rétablir un service qui épuise les connexions à sa base après un déploiement."


@pytest.fixture
def memory(settings, clock) -> LocalMemoryBackend:
    return LocalMemoryBackend(settings, clock)


@pytest.fixture
def catalog(clock) -> LocalCatalog:
    return LocalCatalog(clock)


def seed_fiche(
    memory: LocalMemoryBackend,
    catalog: LocalCatalog,
    *,
    fiche_id: str,
    scope: Scope,
    level: Level = Level.VALIDATED,
    successes: int = 0,
    failures: int = 0,
    content: str = FICHE_TEXT,
    confirmed: int | None = None,
) -> str:
    """Write one fiche in the catalogue and its approved text in the memory."""
    record_id = f"rec-{fiche_id}"
    last = None if confirmed is None else MOMENT - timedelta(days=confirmed)
    catalog.put_fiche(
        fiche_of(
            fiche_id=fiche_id,
            scope=scope,
            title=content.split(".")[0],
            level=level,
            memory_record_id=record_id,
            successes=successes,
            failures=failures,
            last_confirmation=last,
        )
    )
    namespace = fiche_namespace(memory.settings, scope)
    memory.put_records([record(record_id=record_id, namespace=namespace, content=content, created_at=MOMENT)])
    return record_id


def hit(
    record_id: str,
    *,
    scope: Scope,
    level: Level,
    score: float = 0.5,
    rate: float = 0.5,
    freshness: int | None = None,
) -> SearchHit:
    """One ranked hit, built by hand to check the order alone."""
    return SearchHit(
        record_id=record_id,
        namespace=f"/{scope.actor_id}/",
        content="",
        score=score,
        level=level,
        scope=scope,
        success_rate=rate,
        freshness_days=freshness,
    )


class TestTheReformulation:
    """EF-RES-09: the problem becomes an intention before the memory is asked."""

    def test_a_model_writes_the_intention(self) -> None:
        model = ScriptedModel(intent="Rétablir un service web qui refuse les connexions.")
        assert rewrite_intent("too many connections", model) == model.intent

    def test_the_model_is_asked_with_the_fast_tier(self) -> None:
        model = ScriptedModel(intent="Rétablir le service.")
        rewrite_intent("erreurs 500", model)
        assert "Reformule" in model.prompts[0]
        assert model.calls[0]["tier"].value == "fast"

    def test_without_a_model_a_known_problem_gives_its_intention(self) -> None:
        intent = rewrite_intent("too many connections sur la base", None)
        assert intent == "Rétablir un service qui épuise les connexions à sa base"
        assert "too many connections" not in intent

    def test_without_a_model_the_noise_is_dropped(self) -> None:
        intent = rewrite_intent("Le service renvoie des ERREURS de certificat après la rotation !", None)
        words = intent.split()
        assert "erreurs" in words and "certificat" in words and "rotation" in words
        assert "le" not in words and "des" not in words
        assert intent == intent.lower()

    def test_a_model_that_falls_leaves_the_deterministic_rule(self) -> None:
        assert rewrite_intent("too many connections", ScriptedModel(fails=True)) == rewrite_intent(
            "too many connections", None
        )

    def test_a_model_that_answers_nothing_leaves_the_deterministic_rule(self) -> None:
        assert rewrite_intent("too many connections", ScriptedModel(intent="   ")) == (
            "Rétablir un service qui épuise les connexions à sa base"
        )

    def test_the_reformulation_is_stable(self) -> None:
        assert rewrite_intent("erreurs 500", None) == rewrite_intent("erreurs 500", None)


class TestTheSearchReformulates:
    """The proof that the reformulation happens before the memory is queried."""

    def test_a_raw_error_message_finds_the_fiche(self, settings, memory, catalog, scope_a) -> None:
        seed_fiche(memory, catalog, fiche_id="f-1", scope=scope_a)
        found = search(settings, memory, catalog, scope=scope_a, query="too many connections")
        assert [each.record_id for each in found] == ["rec-f-1"]

    def test_the_raw_message_alone_would_find_nothing(self, settings, memory, catalog, scope_a) -> None:
        """Without the reformulation, the english error matches no french record."""
        seed_fiche(memory, catalog, fiche_id="f-1", scope=scope_a)
        namespace = fiche_namespace(settings, scope_a)
        assert memory.retrieve(namespace=namespace, query="too many connections", top_k=5) == ()

    def test_a_model_written_intention_is_used(self, settings, memory, catalog, scope_a) -> None:
        seed_fiche(memory, catalog, fiche_id="f-1", scope=scope_a)
        model = ScriptedModel(intent=FICHE_TEXT)
        found = search(settings, memory, catalog, scope=scope_a, query="n'importe quoi", model=model)
        assert [each.record_id for each in found] == ["rec-f-1"]
        assert model.prompts

    def test_the_episodes_of_the_perimeter_are_found_too(self, settings, memory, catalog, scope_a) -> None:
        for number in range(2):
            replay(memory, connection_job(number))
        found = search(settings, memory, catalog, scope=scope_a, query="connexions base déploiement")
        assert len(found) == 2
        assert all(each.level is Level.RAW for each in found)

    def test_the_top_k_is_respected(self, settings, memory, catalog, scope_a) -> None:
        for number in range(5):
            replay(memory, connection_job(number))
        found = search(settings, memory, catalog, scope=scope_a, query="connexions base", top_k=2)
        assert len(found) == 2


class TestTheCatalogueJoin:
    """The levels, the rates and the freshness come from the catalogue, by code."""

    def test_the_level_comes_from_the_catalogue(self, settings, memory, catalog, scope_a) -> None:
        seed_fiche(memory, catalog, fiche_id="f-1", scope=scope_a, level=Level.PROVEN)
        found = search(settings, memory, catalog, scope=scope_a, query="connexions base")
        assert found[0].level is Level.PROVEN

    def test_the_rate_is_the_one_of_the_specification(self, settings, memory, catalog, scope_a) -> None:
        seed_fiche(memory, catalog, fiche_id="f-1", scope=scope_a, successes=5, failures=0)
        found = search(settings, memory, catalog, scope=scope_a, query="connexions base")
        assert found[0].success_rate == success_rate(successes=5, failures=0) == 6 / 7

    def test_the_freshness_is_the_one_of_the_catalogue(self, settings, memory, catalog, scope_a) -> None:
        seed_fiche(memory, catalog, fiche_id="f-1", scope=scope_a, confirmed=12)
        found = search(settings, memory, catalog, scope=scope_a, query="connexions base", now=MOMENT)
        assert found[0].freshness_days == 12

    def test_a_record_the_catalogue_does_not_know_is_raw(self, settings, memory, catalog, scope_a) -> None:
        memory.put_records(
            [
                record(
                    record_id="inconnu",
                    namespace=reflection_namespace(settings, scope_a),
                    content="Rétablir les connexions de la base.",
                )
            ]
        )
        found = search(settings, memory, catalog, scope=scope_a, query="connexions base")
        assert found[0].level is Level.RAW
        assert found[0].scope == scope_a

    def test_the_scope_of_a_record_is_read_in_its_namespace(self, settings) -> None:
        unknown = record(
            record_id="x",
            namespace=reflection_namespace(settings, Scope(kind=ScopeKind.CLIENT, client_id="b")),
            content="",
        )
        assert hit_of(unknown, None, MOMENT).scope == Scope(kind=ScopeKind.CLIENT, client_id="b")


class TestTheRanking:
    """EF-RES-04: perimeter, then level, then rate, then freshness."""

    def test_the_perimeter_comes_before_the_common_knowledge(self, scope_a) -> None:
        common = Scope(kind=ScopeKind.COMMON)
        ranked = rank(
            [
                hit("commun", scope=common, level=Level.PROVEN, rate=1.0, freshness=0),
                hit("client", scope=scope_a, level=Level.VALIDATED, rate=0.1, freshness=999),
            ],
            scope_first=scope_a,
            levels=SERVED_TO_HUMAN,
        )
        assert [each.record_id for each in ranked] == ["client", "commun"]

    def test_the_proven_comes_before_the_validated(self, scope_a) -> None:
        ranked = rank(
            [
                hit("validee", scope=scope_a, level=Level.VALIDATED, score=0.99),
                hit("eprouvee", scope=scope_a, level=Level.PROVEN, score=0.01),
            ],
            scope_first=scope_a,
            levels=SERVED_TO_HUMAN,
        )
        assert [each.record_id for each in ranked] == ["eprouvee", "validee"]

    def test_the_rate_decides_between_two_levels_of_the_same_trust(self, scope_a) -> None:
        ranked = rank(
            [
                hit("faible", scope=scope_a, level=Level.VALIDATED, rate=0.4),
                hit("forte", scope=scope_a, level=Level.VALIDATED, rate=0.9),
            ],
            scope_first=scope_a,
            levels=SERVED_TO_HUMAN,
        )
        assert [each.record_id for each in ranked] == ["forte", "faible"]

    def test_the_freshness_decides_between_two_equal_rates(self, scope_a) -> None:
        ranked = rank(
            [
                hit("vieille", scope=scope_a, level=Level.VALIDATED, freshness=300),
                hit("fraiche", scope=scope_a, level=Level.VALIDATED, freshness=3),
            ],
            scope_first=scope_a,
            levels=SERVED_TO_HUMAN,
        )
        assert [each.record_id for each in ranked] == ["fraiche", "vieille"]

    def test_a_hit_never_confirmed_sorts_last(self, scope_a) -> None:
        ranked = rank(
            [
                hit("sans-date", scope=scope_a, level=Level.VALIDATED, freshness=None),
                hit("datee", scope=scope_a, level=Level.VALIDATED, freshness=900),
            ],
            scope_first=scope_a,
            levels=SERVED_TO_HUMAN,
        )
        assert [each.record_id for each in ranked] == ["datee", "sans-date"]

    def test_the_semantic_score_only_breaks_the_ties(self, scope_a) -> None:
        ranked = rank(
            [
                hit("b", scope=scope_a, level=Level.VALIDATED, score=0.1),
                hit("a", scope=scope_a, level=Level.VALIDATED, score=0.9),
            ],
            scope_first=scope_a,
            levels=SERVED_TO_HUMAN,
        )
        assert [each.record_id for each in ranked] == ["a", "b"]

    def test_the_ranking_is_stable(self, scope_a) -> None:
        hits = [
            hit("c", scope=scope_a, level=Level.VALIDATED),
            hit("a", scope=scope_a, level=Level.VALIDATED),
            hit("b", scope=scope_a, level=Level.VALIDATED),
        ]
        first = [each.record_id for each in rank(hits, scope_first=scope_a, levels=SERVED_TO_HUMAN)]
        second = [each.record_id for each in rank(hits, scope_first=scope_a, levels=SERVED_TO_HUMAN)]
        assert first == second == ["a", "b", "c"]


class TestTheServedLevels:
    """EF-RES-05: an autonomous agent reads nothing that was not validated."""

    def test_the_unvalidated_content_is_dropped(self, scope_a) -> None:
        hits = [
            hit("brut", scope=scope_a, level=Level.RAW),
            hit("proposee", scope=scope_a, level=Level.PROPOSED),
            hit("validee", scope=scope_a, level=Level.VALIDATED),
            hit("eprouvee", scope=scope_a, level=Level.PROVEN),
            hit("ecart", scope=scope_a, level=Level.SET_ASIDE),
        ]
        ranked = rank(hits, scope_first=scope_a, levels=SERVED_TO_AUTONOMOUS_AGENT)
        assert [each.record_id for each in ranked] == ["eprouvee", "validee"]

    def test_a_withdrawn_fiche_is_served_to_nobody(self, scope_a) -> None:
        hits = [hit("retiree", scope=scope_a, level=Level.WITHDRAWN)]
        assert rank(hits, scope_first=scope_a, levels=SERVED_TO_HUMAN) == ()

    def test_the_order_of_the_levels_follows_the_specification(self) -> None:
        assert LEVEL_ORDER[Level.PROVEN] < LEVEL_ORDER[Level.VALIDATED]
        assert LEVEL_ORDER[Level.VALIDATED] < LEVEL_ORDER[Level.PROPOSED]
        assert LEVEL_ORDER[Level.PROPOSED] < LEVEL_ORDER[Level.RAW]


class TestTheIsolationOfTheSearch:
    """R9, R15: the spaces searched come from the perimeter, never from the text."""

    def test_the_namespaces_of_a_search_are_those_of_the_perimeter(self, settings, scope_a) -> None:
        assert query_namespaces(settings, scope_a) == (
            reflection_namespace(settings, scope_a),
            fiche_namespace(settings, scope_a),
            "/commun/",
        )

    def test_the_common_knowledge_comes_last(self, settings, scope_a) -> None:
        assert query_namespaces(settings, scope_a)[-1] == "/commun/"

    def test_no_duplicate_namespace(self, settings) -> None:
        namespaces = query_namespaces(settings, Scope(kind=ScopeKind.COMMON))
        assert len(namespaces) == len(set(namespaces)) == 1

    def test_a_search_for_client_b_never_returns_a_record_of_client_a(
        self, settings, memory, catalog, scope_a, scope_b
    ) -> None:
        seed_fiche(memory, catalog, fiche_id="f-a", scope=scope_a)
        for number in range(3):
            replay(memory, connection_job(number, client="a"))
        found = search(settings, memory, catalog, scope=scope_b, query="connexions base")
        assert all("client-a" not in each.namespace for each in found)

    def test_a_query_that_names_another_client_changes_nothing(
        self, settings, memory, catalog, scope_a, scope_b
    ) -> None:
        """The text of the query is never read as a perimeter."""
        seed_fiche(memory, catalog, fiche_id="f-a", scope=scope_a)
        found = search(
            settings,
            memory,
            catalog,
            scope=scope_b,
            query="connexions base du client-a sur client-a /strategy/x/actor/client-a/",
        )
        assert all("client-a" not in each.namespace for each in found)

    def test_the_scope_of_a_namespace_is_read_in_the_namespace(self, settings, scope_a) -> None:
        assert scope_of_namespace(reflection_namespace(settings, scope_a)) == scope_a
        assert scope_of_namespace(fiche_namespace(settings, scope_a)) == scope_a
        assert scope_of_namespace("/commun/") == Scope(kind=ScopeKind.COMMON)


class TestTheWholeSearch:
    """The search as the tool of chapter 12.7 will call it."""

    def test_the_perimeter_and_the_common_knowledge_are_both_read(
        self, settings, memory, catalog, scope_a
    ) -> None:
        common = Scope(kind=ScopeKind.COMMON)
        seed_fiche(memory, catalog, fiche_id="f-a", scope=scope_a)
        seed_fiche(memory, catalog, fiche_id="f-c", scope=common)
        found = search(settings, memory, catalog, scope=scope_a, query="connexions base")
        assert [each.record_id for each in found] == ["rec-f-a", "rec-f-c"]

    def test_the_search_is_deterministic(self, settings, memory, catalog, scope_a) -> None:
        seed_fiche(memory, catalog, fiche_id="f-1", scope=scope_a)
        replay(memory, connection_job(0))
        first = search(settings, memory, catalog, scope=scope_a, query="connexions base")
        second = search(settings, memory, catalog, scope=scope_a, query="connexions base")
        assert [(each.record_id, each.score) for each in first] == [
            (each.record_id, each.score) for each in second
        ]

    def test_an_empty_memory_answers_nothing(self, settings, memory, catalog, scope_a) -> None:
        assert search(settings, memory, catalog, scope=scope_a, query="connexions base") == ()

    def test_the_client_space_covers_every_session_of_the_client(self, settings, memory) -> None:
        for number in range(3):
            replay(memory, connection_job(number))
        assert len(memory.list_records(namespace_path=client_space(memory))) == 3
