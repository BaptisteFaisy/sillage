"""Distillation and reflection: the episodes, the lessons, and the isolation rule.

What is checked here is the heart of the design:

* EF-DIS-01 to EF-DIS-07: one episode per finished job, its verdict and its
  justification, its failed paths kept apart, its link to the origin, and the
  redistillation of a corrected trace;
* EF-DIS-02: a reflection never crosses two clients - the assertion stops it;
* R5: three close episodes of one client give one lesson linked to the three;
* ENF-IA-01: a model writes text, and a figure it tries to slip in is dropped;
* ENF-RH-01: no lesson is about a person, and no author splits a reflection.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from memory_fakes import (
    CLOSED_WITHOUT_SUCCESS,
    MOMENT,
    ScriptedModel,
    connection_job,
    job_trace,
    proxy_job,
    record,
)
from sillage.kernel.ids import episode_id
from sillage.kernel.types import AuthorKind, Level, Outcome, Scope, ScopeKind
from sillage.memory.distill import (
    MIN_EPISODES_FOR_A_LESSON,
    confidence_of,
    distil_episode,
    episode_content,
    failed_paths_of,
    lesson_from_records,
    reflect,
    teachings_of,
)
from sillage.memory.grouping import family_of

#: The answer of a model that behaves: text, and only text.
GOOD_DISTIL = json.dumps(
    {
        "situation": "Un service web épuise les connexions de sa base.",
        "intention": "Rétablir le service en revenant au réglage d'avant le déploiement.",
        "justification": "Le réglage du groupe de connexions expliquait la saturation.",
        "enseignements": ["Vérifier le réglage du groupe de connexions après un déploiement."],
        "pistes": ["Redémarrer le service ne libère pas les connexions durablement."],
    },
    ensure_ascii=False,
)

#: The answer of a model that tries to write the figures itself.
CHEATING_DISTIL = json.dumps(
    {
        "situation": "Un service web épuise les connexions de sa base.",
        "intention": "Rétablir le service.",
        "verdict": "failure",
        "note": 0.97,
        "taux": 1.0,
        "enseignements": [],
        "pistes": [],
    },
    ensure_ascii=False,
)


def close_episodes(client: str = "a", count: int = 3):
    """Three episodes of one client, all about the connections of a database."""
    return tuple(distil_episode(connection_job(number, client=client)) for number in range(count))


class TestDistilEpisode:
    """EF-DIS-01, 03, 04, 05, 07: what one finished job becomes."""

    def test_the_episode_carries_the_identity_of_the_job(self) -> None:
        trace = connection_job(0)
        episode = distil_episode(trace)
        assert episode.episode_id == episode_id("client-a", trace.work_item_id)
        assert episode.work_item_id == trace.work_item_id
        assert episode.scope == trace.scope

    def test_the_episode_tells_whether_the_job_worked(self) -> None:
        episode = distil_episode(connection_job(0))
        assert episode.outcome is Outcome.SUCCESS
        assert episode.justification

    def test_a_failed_job_says_so(self) -> None:
        trace = job_trace(
            work_item_id="guichet:ticket-9000",
            situation="La base refuse les connexions.",
            steps=(("Je redémarre la base.", "La base ne répond toujours pas."),),
            closing=CLOSED_WITHOUT_SUCCESS,
        )
        episode = distil_episode(trace)
        assert episode.outcome is Outcome.FAILURE
        assert "failure" in episode.justification

    def test_the_story_is_told_turn_by_turn(self) -> None:
        trace = connection_job(0)
        episode = distil_episode(trace)
        assert len(episode.turns) == len(trace.events) // 2 - 1
        assert episode.turns[0].action and episode.turns[0].result

    def test_the_failed_paths_are_kept_apart(self) -> None:
        """EF-DIS-04: the wrong lead stays in the episode, on its own."""
        episode = distil_episode(connection_job(0))
        assert episode.failed_paths
        assert any("ne règle rien" in path for path in episode.failed_paths)
        assert not set(episode.failed_paths) & set(episode.teachings)

    def test_the_teachings_are_the_steps_that_worked(self) -> None:
        episode = distil_episode(connection_job(0))
        assert episode.teachings
        assert all(teaching not in episode.failed_paths for teaching in episode.teachings)

    def test_a_fresh_episode_is_raw(self) -> None:
        assert distil_episode(connection_job(0)).level is Level.RAW

    def test_the_author_stays_a_pseudonym(self) -> None:
        """ENF-RH-01: the episode carries a pseudonym, and no figure per person."""
        episode = distil_episode(connection_job(0))
        assert episode.author == "p-7f3a"
        assert episode.author_kind is AuthorKind.HUMAN

    def test_the_episode_is_linked_to_its_memory_record(self) -> None:
        trace = connection_job(0)
        records = [
            record(
                record_id="ep-lien",
                namespace="/strategy/episodes_travail/actor/client-a/session/x/",
                metadata={"workItemId": trace.work_item_id},
            )
        ]
        assert distil_episode(trace, records).memory_record_id == "ep-lien"

    def test_the_text_of_the_episode_tells_the_whole_story(self) -> None:
        content = episode_content(connection_job(0))
        for marker in ("Situation :", "Intention :", "Déroulé :", "Verdict :"):
            assert marker in content
        assert "Pistes essayées sans succès :" in content

    def test_EF_DIS_07_a_corrected_trace_gives_the_same_episode(self) -> None:
        """Redistilling replaces the episode instead of adding a second one."""
        trace = connection_job(0)
        first = distil_episode(trace)
        corrected = replace(
            trace,
            events=tuple(
                replace(event, text=event.text.replace("redémarre", "vérifie")) for event in trace.events
            ),
        )
        second = distil_episode(corrected)
        assert second.episode_id == first.episode_id
        assert second.situation != first.situation or second.turns != first.turns

    def test_R11_the_rejected_diagnosis_survives(self) -> None:
        trace = job_trace(
            work_item_id="devops:inv-77",
            steps=(("Diagnostic de l'agent : la base est saturée.", "412 connexions ouvertes."),),
            origin={
                "verdict": "rejeté par",
                "verdictPar": "p-alice",
                "diagnosticRejete": "La base est sous-dimensionnée.",
                "raisonVerdict": "Le réglage du groupe de connexions avait changé.",
            },
        )
        episode = distil_episode(trace)
        assert episode.rejected_diagnosis == "La base est sous-dimensionnée."
        assert episode.corrected_by == "p-alice"
        assert episode.justification == "Le réglage du groupe de connexions avait changé."


class TestTheModelWritesTextOnly:
    """ENF-IA-01: the model may rewrite the words, never the figures."""

    def test_a_model_may_rewrite_the_situation_and_the_intention(self) -> None:
        model = ScriptedModel(distil=GOOD_DISTIL)
        episode = distil_episode(connection_job(0), model=model)
        assert episode.situation == "Un service web épuise les connexions de sa base."
        assert episode.intent.startswith("Rétablir le service")
        assert model.calls and model.calls[0]["json_schema"]

    def test_the_verdict_is_never_written_by_a_model(self) -> None:
        """The model says "failure" and gives a rate: both are ignored."""
        episode = distil_episode(connection_job(0), model=ScriptedModel(distil=CHEATING_DISTIL))
        assert episode.outcome is Outcome.SUCCESS
        assert "0.97" not in episode.situation
        assert episode.level is Level.RAW

    def test_a_figure_in_a_text_field_is_dropped(self) -> None:
        model = ScriptedModel(distil=json.dumps({"situation": 0.9, "intention": 42}))
        episode = distil_episode(connection_job(0), model=model)
        assert episode.situation != "0.9"
        assert episode.intent != "42"

    def test_the_teachings_of_the_model_complete_those_of_the_code(self) -> None:
        episode = distil_episode(connection_job(0), model=ScriptedModel(distil=GOOD_DISTIL))
        assert any("groupe de connexions" in teaching for teaching in episode.teachings)

    def test_a_model_that_fails_does_not_fail_the_distillation(self) -> None:
        episode = distil_episode(connection_job(0), model=ScriptedModel(fails=True))
        assert episode.episode_id == episode_id("client-a", "guichet:ticket-2041")
        assert episode.situation

    def test_a_model_that_answers_garbage_falls_back_on_the_code(self) -> None:
        episode = distil_episode(connection_job(0), model=ScriptedModel(distil="pas du json"))
        assert episode.situation == distil_episode(connection_job(0)).situation

    def test_the_prompt_speaks_the_language_of_the_operations(self) -> None:
        """EF-DIS-06: the instructions carry the vocabulary of the job."""
        model = ScriptedModel(distil=GOOD_DISTIL)
        distil_episode(connection_job(0), model=model)
        prompt = model.prompts[0]
        assert "postgresql" in prompt
        assert "guichet:ticket-2041" in prompt
        assert "exploitation" in model.calls[0]["system"]


@pytest.mark.recette
class TestR5ThreeCloseEpisodesGiveOneLesson:
    """R5: three close episodes of one client give one lesson, linked to the three."""

    def test_one_lesson_linked_to_the_three_episodes(self) -> None:
        lessons = reflect(Scope(kind=ScopeKind.CLIENT, client_id="a"), close_episodes())
        assert len(lessons) == 1
        assert len(lessons[0].episode_ids) == 3

    def test_the_lesson_cites_exactly_those_episodes(self) -> None:
        episodes = close_episodes()
        lesson = reflect(Scope(kind=ScopeKind.CLIENT, client_id="a"), episodes)[0]
        assert set(lesson.episode_ids) == {episode.episode_id for episode in episodes}

    def test_the_lesson_is_a_proposal_of_its_perimeter(self) -> None:
        lesson = reflect(Scope(kind=ScopeKind.CLIENT, client_id="a"), close_episodes())[0]
        assert lesson.level is Level.PROPOSED
        assert lesson.scope == Scope(kind=ScopeKind.CLIENT, client_id="a")

    def test_the_lesson_has_a_use_case_and_advice(self) -> None:
        lesson = reflect(Scope(kind=ScopeKind.CLIENT, client_id="a"), close_episodes())[0]
        assert lesson.use_case
        assert lesson.advice

    def test_the_lesson_keeps_the_traps_apart(self) -> None:
        lesson = reflect(Scope(kind=ScopeKind.CLIENT, client_id="a"), close_episodes())[0]
        assert lesson.pitfalls
        assert any("ne règle rien" in pitfall for pitfall in lesson.pitfalls)

    def test_the_confidence_is_computed_by_code(self) -> None:
        episodes = close_episodes()
        lesson = reflect(Scope(kind=ScopeKind.CLIENT, client_id="a"), episodes)[0]
        successes = sum(1 for episode in episodes if episode.outcome is Outcome.SUCCESS)
        assert lesson.confidence == confidence_of(len(episodes), successes)
        assert 0.1 <= lesson.confidence <= 1.0

    def test_a_model_writes_the_lesson_but_not_its_confidence(self) -> None:
        model = ScriptedModel(
            reflect=json.dumps(
                {
                    "casUsage": "Rétablir un service qui épuise les connexions de sa base.",
                    "conseils": ["Revenir au réglage d'avant le déploiement."],
                    "pieges": ["Ne pas redémarrer en premier."],
                    "confiance": 0.99,
                },
                ensure_ascii=False,
            )
        )
        episodes = close_episodes()
        lesson = reflect(Scope(kind=ScopeKind.CLIENT, client_id="a"), episodes, model)[0]
        assert lesson.use_case.startswith("Rétablir un service")
        assert "confiance" not in lesson.advice
        assert lesson.confidence == confidence_of(3, 3) != 0.99

    def test_one_episode_alone_gives_no_lesson(self) -> None:
        assert reflect(Scope(kind=ScopeKind.CLIENT, client_id="a"), close_episodes(count=1)) == ()
        assert MIN_EPISODES_FOR_A_LESSON == 2

    def test_two_families_never_join_one_lesson(self) -> None:
        episodes = (
            *close_episodes(),
            *(distil_episode(proxy_job(number)) for number in range(3)),
        )
        lessons = reflect(Scope(kind=ScopeKind.CLIENT, client_id="a"), episodes)
        assert len(lessons) == 2
        assert all(len(lesson.episode_ids) == 3 for lesson in lessons)
        assert not set(lessons[0].episode_ids) & set(lessons[1].episode_ids)

    def test_the_reflection_is_deterministic(self) -> None:
        scope = Scope(kind=ScopeKind.CLIENT, client_id="a")
        episodes = close_episodes()
        assert [lesson.lesson_id for lesson in reflect(scope, episodes)] == [
            lesson.lesson_id for lesson in reflect(scope, episodes)
        ]

    def test_the_lesson_keeps_no_figure_about_a_person(self) -> None:
        """ENF-RH-01: two authors in one perimeter do not split the lesson."""
        episodes = tuple(
            distil_episode(
                connection_job(number, client="a"),
            )
            for number in range(3)
        )
        episodes = (
            replace(episodes[0], author="p-alice"),
            replace(episodes[1], author="p-bob"),
            replace(episodes[2], author="p-carol"),
        )
        lessons = reflect(Scope(kind=ScopeKind.CLIENT, client_id="a"), episodes)
        assert len(lessons) == 1
        text = " ".join([lessons[0].use_case, *lessons[0].advice, *lessons[0].pitfalls])
        assert "p-alice" not in text and "p-bob" not in text


class TestEFDIS02TheReflectionNeverCrossesTwoClients:
    """The isolation rule is an assertion, not a convention."""

    def test_an_episode_of_another_client_stops_the_reflection(self, scope_a) -> None:
        episodes = (*close_episodes("a"), *close_episodes("b", count=1))
        with pytest.raises(AssertionError) as caught:
            reflect(scope_a, episodes)
        assert "EF-DIS-02" in str(caught.value)

    def test_the_message_names_the_two_perimeters(self, scope_a) -> None:
        with pytest.raises(AssertionError) as caught:
            reflect(scope_a, close_episodes("b"))
        assert "client-b" in str(caught.value) and "client-a" in str(caught.value)

    def test_two_clients_give_two_lessons(self, scope_a, scope_b) -> None:
        mine = reflect(scope_a, close_episodes("a"))
        theirs = reflect(scope_b, close_episodes("b"))
        assert len(mine) == 1 and len(theirs) == 1
        assert mine[0].lesson_id != theirs[0].lesson_id
        assert not set(mine[0].episode_ids) & set(theirs[0].episode_ids)

    def test_the_common_perimeter_is_a_perimeter_like_the_others(self) -> None:
        common = Scope(kind=ScopeKind.COMMON)
        episodes = tuple(
            replace(episode, scope=common, episode_id=f"co-{number}")
            for number, episode in enumerate(close_episodes())
        )
        lessons = reflect(common, episodes)
        assert len(lessons) == 1
        assert lessons[0].scope == common


class TestLessonFromRecords:
    """The offline reflection, straight from the records of one perimeter."""

    def test_close_records_give_one_lesson(self, scope_a) -> None:
        records = [
            record(
                record_id=f"ep-{number}",
                namespace="/strategy/episodes_travail/actor/client-a/session/x/",
                content="Rétablir les connexions de la base après un déploiement.",
                metadata={"scope": "client-a", "techno": "postgresql", "intent": "Rétablir les connexions."},
                created_at=MOMENT,
            )
            for number in range(3)
        ]
        lessons = lesson_from_records(scope_a, records)
        assert len(lessons) == 1
        assert lessons[0].episode_ids == ("ep-0", "ep-1", "ep-2")
        assert lessons[0].use_case == "Rétablir les connexions."

    def test_a_record_of_another_perimeter_is_refused(self, scope_a) -> None:
        foreign = record(
            record_id="ep-b",
            namespace="/strategy/episodes_travail/actor/client-b/session/x/",
            content="Autre chose.",
            metadata={"scope": "client-b"},
        )
        with pytest.raises(AssertionError) as caught:
            lesson_from_records(scope_a, [foreign])
        assert "EF-DIS-02" in str(caught.value)

    def test_one_record_alone_gives_no_lesson(self, scope_a) -> None:
        only = record(
            record_id="ep-0",
            namespace="/commun/",
            content="Rétablir les connexions de la base.",
            metadata={"scope": "client-a", "techno": "postgresql"},
        )
        assert lesson_from_records(scope_a, [only]) == ()


class TestTheConfidenceIsComputedByCode:
    """ENF-IA-01: the confidence of a lesson is a figure of the code."""

    def test_nothing_at_all_reads_the_bottom(self) -> None:
        assert confidence_of(0, 0) == 0.1

    def test_it_grows_with_the_episodes(self) -> None:
        assert confidence_of(2, 2) < confidence_of(3, 3) < confidence_of(4, 4)

    def test_it_never_reaches_certainty(self) -> None:
        assert confidence_of(50, 50) < 1.0

    def test_a_lesson_of_failures_stays_low(self) -> None:
        assert confidence_of(3, 0) < confidence_of(3, 3)

    def test_it_stays_inside_the_range_of_the_specification(self) -> None:
        for count in range(0, 20):
            for successes in range(0, count + 1):
                assert 0.1 <= confidence_of(count, successes) <= 1.0


class TestTheTechnologicalFamilies:
    """Two episodes of another family never join a lesson."""

    def test_a_known_technology_gives_its_family(self) -> None:
        assert family_of(("postgresql",)) == "database"
        assert family_of(("mysql", "ecs")) == "database"
        assert family_of(("nginx",)) == "proxy"

    def test_the_first_technology_decides(self) -> None:
        assert family_of(("nginx", "postgresql")) == "proxy"

    def test_an_unknown_technology_gives_its_own_name(self) -> None:
        assert family_of(("Outil-Maison",)) == "outil-maison"

    def test_no_technology_gives_the_unknown_family(self) -> None:
        assert family_of(()) == "unknown"
        assert family_of(("",)) == "unknown"


def test_the_failed_paths_and_the_teachings_are_read_from_the_turns() -> None:
    trace = connection_job(0)
    assert failed_paths_of(trace)
    assert teachings_of(trace)
    assert not set(failed_paths_of(trace)) & set(teachings_of(trace))
