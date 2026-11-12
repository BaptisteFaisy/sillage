"""The life of a piece of knowledge: declared, proposed, validated, served, rated.

The scenarios of this file walk the loop of the specification: R3 (the same
trace twice), R7 (an engineer tries to validate), R10 (an agent investigates a
known fault and reports back), R11 (a human rejects the diagnosis of an agent),
R13, R14 and R15 (the passage to the common knowledge), R16 (two negative
feedbacks), and the requirements EF-VAL-01, EF-VAL-02, EF-RET-04 and
EF-RET-05.
"""

from __future__ import annotations

from sillage.capture import sample
from sillage.catalog.scoring import success_rate
from sillage.kernel.errors import LeakDetected
from sillage.kernel.types import (
    AgentMode,
    Claim,
    Decision,
    Level,
    PrincipalRole,
    Scope,
    ScopeKind,
)
from sillage.tools import (
    propose_fiche,
    publish_common,
    recall,
    record_episode,
    report_outcome,
    validate_fiche,
)
from sillage.tools.errors import is_refusal
from tools_fakes import CLIENT_A, CLIENT_B, add_fiche, add_lesson, build_stack

#: The problem the demo asks about.
PROBLEM = "erreurs 500, too many connections après un déploiement"

#: The common text, without any element of the client.
COMMON_TEXT = (
    "Quand un service refuse ses connexions, vérifier le groupe de connexions avant la taille de la base."
)


def test_the_same_declaration_twice_gives_one_episode():
    """R3, EF-CAP-11: a trace already received is not treated twice."""
    stack = build_stack()
    agent = stack.principal(PrincipalRole.AGENT, CLIENT_A, AgentMode.AUTONOMOUS)
    declaration = {
        "client_id": CLIENT_A,
        "work_item_id": "devoops-agent:enquete-9001",
        "situation": "Le site renvoie des erreurs 500 depuis 14h02.",
        "intent": "Rétablir le service en gardant les connexions ouvertes.",
        "attempts": ["Comparer le groupe de connexions avant et après le déploiement"],
        "outcome": "success",
        "evidence": ["Connexions actives après correctif : 38."],
    }

    first = record_episode(stack, **declaration, principal=agent)
    second = record_episode(stack, **declaration, principal=agent)

    assert not is_refusal(first)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert first["episodeId"] == second["episodeId"]
    episodes = tuple(stack.catalog.list_episodes(scope=Scope(kind=ScopeKind.CLIENT, client_id=CLIENT_A)))
    assert len([one for one in episodes if one.work_item_id == declaration["work_item_id"]]) == 1


def test_an_engineer_cannot_validate_a_fiche():
    """R7: the validation is the referent's, and the refusal is in the journal."""
    stack = build_stack()
    fiche = add_fiche(stack, level=Level.PROPOSED)
    engineer = stack.principal(PrincipalRole.ENGINEER, CLIENT_A)

    answer = validate_fiche(stack, fiche_id=fiche.fiche_id, decision="validate", principal=engineer)

    assert is_refusal(answer)
    assert "M.2" in answer["message"]
    refusals = stack.journal.query(tool="validate_fiche", decision=Decision.FORBID)
    assert refusals


def test_an_agent_investigates_a_known_fault_and_reports_back():
    """R10: recall is called, the fiche is served, the feedback is recorded."""
    stack = build_stack()
    fiche = add_fiche(stack)
    agent = stack.principal(PrincipalRole.AGENT, CLIENT_A, AgentMode.AUTONOMOUS)

    answer = recall(stack, client_id=CLIENT_A, problem=PROBLEM, principal=agent)
    assert not is_refusal(answer)
    assert [view.fiche.fiche_id for view in answer.fiches] == [fiche.fiche_id]

    report = report_outcome(
        stack,
        usage_id=answer.usage_id,
        result="helped",
        note="les étapes ont réglé la panne",
        principal=agent,
    )

    assert not is_refusal(report)
    assert report["fiches"] == [fiche.fiche_id]
    stored = stack.catalog.get_fiche(fiche.fiche_id)
    assert stored.success_count == 1
    assert stored.failure_count == 0
    assert stored.no_feedback_count == 0
    assert stack.catalog.list_feedback(fiche.fiche_id)


def test_the_episode_keeps_the_rejected_diagnosis_and_the_right_one():
    """R11: the correction of a human stays with the episode, and the trap with the fiche."""
    stack = build_stack()
    report = stack.ingest.ingest_trace(sample.investigation_77())
    episode = stack.catalog.get_episode(report.episode_id)

    assert episode is not None
    assert episode.rejected_diagnosis == "la base est saturée, il faut augmenter sa taille"
    assert episode.corrected_by
    fiche = add_fiche(
        stack,
        title="Base saturée après un déploiement : vérifier les connexions",
        avoid=("Augmenter la taille de la base avant d'avoir vérifié le groupe de connexions",),
    )
    answer = recall(stack, client_id=CLIENT_A, problem=PROBLEM)

    assert [view.fiche.fiche_id for view in answer.fiches] == [fiche.fiche_id]
    assert "Augmenter la taille de la base" in answer.avoid[0]


def test_a_fiche_with_an_unsourced_claim_is_refused():
    """EF-VAL-02: a statement that cites no episode blocks the validation."""
    stack = build_stack()
    fiche = add_fiche(stack, level=Level.PROPOSED, claims=(Claim(text="Une affirmation"),))
    referent = stack.principal(PrincipalRole.REFERENT, CLIENT_A)

    answer = validate_fiche(stack, fiche_id=fiche.fiche_id, decision="validate", principal=referent)

    assert is_refusal(answer)
    assert answer["error"] == "unsourced_claim"
    assert stack.catalog.get_fiche(fiche.fiche_id).level is Level.PROPOSED


def test_the_referent_validates_a_fiche_proposed_by_an_agent():
    """EF-VAL-01, EF-VAL-03: the draft waits for a human, and becomes validated."""
    stack = build_stack()
    lesson = add_lesson(stack)
    agent = stack.principal(PrincipalRole.AGENT, CLIENT_A, AgentMode.ASSISTED)
    referent = stack.principal(PrincipalRole.REFERENT, CLIENT_A)

    draft = propose_fiche(
        stack,
        lesson_id=lesson.lesson_id,
        draft={"title": "Connexions à la base épuisées", "steps": ["Comparer le pool"]},
        principal=agent,
    )
    assert not is_refusal(draft)
    assert draft.level is Level.PROPOSED

    validated = validate_fiche(stack, fiche_id=draft.fiche_id, decision="validate", principal=referent)

    assert not is_refusal(validated)
    assert validated.level is Level.VALIDATED
    assert validated.validated_by
    assert stack.catalog.get_fiche(draft.fiche_id).level is Level.VALIDATED


def test_the_common_version_is_refused_while_an_identifier_survives():
    """R13: one identifier left in the rewrite refuses the publication."""
    stack = build_stack()
    fiche = add_fiche(stack)
    referent = stack.principal(PrincipalRole.REFERENT, CLIENT_A)

    answer = publish_common(
        stack,
        fiche_id=fiche.fiche_id,
        common_text="Chez Client A - retail, augmenter la taille de la base ne règle rien.",
        principal=referent,
    )

    assert is_refusal(answer)
    assert answer["error"] == "leak_detected"
    assert stack.catalog.list_lessons(scope=Scope(kind=ScopeKind.COMMON)) == ()


def test_a_client_marked_no_common_never_feeds_the_common_knowledge():
    """R14: the contract of the client comes before the quality of the rewrite."""
    stack = build_stack()
    stack.catalog.put_client(
        stack.catalog.get_client(CLIENT_A).__class__(
            client_id=CLIENT_A, name="Client A - retail", no_common=True
        )
    )
    fiche = add_fiche(stack)
    referent = stack.principal(PrincipalRole.REFERENT, CLIENT_A)

    answer = publish_common(stack, fiche_id=fiche.fiche_id, common_text=COMMON_TEXT, principal=referent)

    assert is_refusal(answer)
    assert answer["error"] == "common_sharing_refused"


def test_the_common_lesson_cannot_lead_back_to_the_origin_episode():
    """R15: the published text goes to the common space, without a backlink."""
    stack = build_stack()
    fiche = add_fiche(stack)
    referent = stack.principal(PrincipalRole.REFERENT, CLIENT_A)

    lesson = publish_common(stack, fiche_id=fiche.fiche_id, common_text=COMMON_TEXT, principal=referent)

    assert not is_refusal(lesson)
    assert lesson.scope.is_common
    assert lesson.episode_ids == ()
    assert lesson.lesson_id != fiche.fiche_id
    assert fiche.fiche_id not in lesson.advice[0]
    engineer_b = stack.principal(PrincipalRole.ENGINEER, CLIENT_B)
    answer = recall(stack, client_id=CLIENT_B, problem=PROBLEM, principal=engineer_b)
    assert answer.usage_id
    assert not [one for one in answer.fiches if one.fiche.scope.kind is ScopeKind.CLIENT]


def test_two_negative_feedbacks_set_the_fiche_aside_and_warn_the_referent():
    """R16, EF-VAL-06: the fiche goes aside, the referent is warned, the agents lose it."""
    stack = build_stack()
    fiche = add_fiche(stack)
    agent = stack.principal(PrincipalRole.AGENT, CLIENT_A, AgentMode.AUTONOMOUS)

    first = recall(stack, client_id=CLIENT_A, problem=PROBLEM, principal=agent)
    report_outcome(stack, usage_id=first.usage_id, result="did_not_help", principal=agent)
    second = recall(stack, client_id=CLIENT_A, problem=PROBLEM, principal=agent)
    report_outcome(stack, usage_id=second.usage_id, result="did_not_help", principal=agent)

    stored = stack.catalog.get_fiche(fiche.fiche_id)
    assert stored.level is Level.SET_ASIDE
    assert stored.consecutive_failures == 2
    messages = stack.notifier.messages()
    assert messages
    assert fiche.fiche_id in messages[-1].body

    after = recall(stack, client_id=CLIENT_A, problem=PROBLEM, principal=agent)
    assert not is_refusal(after)
    assert [view.fiche.fiche_id for view in after.fiches] == []


def test_the_rate_is_computed_by_the_code():
    """EF-RET-04, EF-RET-05: the rate counts the answers, the pending ones stay apart."""
    stack = build_stack()
    fiche = add_fiche(stack)
    engineer = stack.principal(PrincipalRole.ENGINEER, CLIENT_A)

    pending = recall(stack, client_id=CLIENT_A, problem=PROBLEM, principal=engineer)
    assert stack.catalog.get_fiche(fiche.fiche_id).no_feedback_count == 1

    report_outcome(stack, usage_id=pending.usage_id, result="helped", principal=engineer)
    stored = stack.catalog.get_fiche(fiche.fiche_id)

    assert stored.success_count == 1
    assert stored.failure_count == 0
    assert stored.no_feedback_count == 0
    assert abs(success_rate(successes=stored.success_count, failures=stored.failure_count) - 2 / 3) < 1e-9


def test_the_leak_of_a_common_version_is_an_error_and_not_an_exception():
    """The refusal is returned, never raised: the caller is never broken."""
    stack = build_stack()
    fiche = add_fiche(stack)
    referent = stack.principal(PrincipalRole.REFERENT, CLIENT_A)

    try:
        publish_common(
            stack, fiche_id=fiche.fiche_id, common_text="db-a1.clienta.internal", principal=referent
        )
    except LeakDetected as exc:  # pragma: no cover - the tool must not raise
        raise AssertionError("le refus doit être retourné, pas levé") from exc
