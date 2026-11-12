"""The consultation: what a caller may know, and what it may not (EF-RES-*).

The scenarios of this file are the ones of the specification that concern the
serving: R8 (an autonomous agent asks for the unvalidated), R9 (an agent of
client B asks for client A), R10 (an agent investigates a known fault), R12
(a lesson learnt the day before by another agent), and the requirements
EF-RES-01 to EF-RES-05, EF-PERF-03 and EF-RET-01.
"""

from __future__ import annotations

from sillage.kernel.errors import BackendUnavailable
from sillage.kernel.types import AgentMode, Decision, Level, PrincipalRole, Scope, ScopeKind
from sillage.tools import recall, recall_all
from sillage.tools.errors import is_refusal
from sillage.tools.views import fiche_view
from tools_fakes import CLIENT_A, CLIENT_B, add_fiche, add_lesson, build_stack

#: The problem the demo asks about.
PROBLEM = "erreurs 500, too many connections après un déploiement"


def test_recall_serves_the_validated_fiche_of_the_perimeter():
    """EF-RES-01, EF-RES-03: the fiche comes back with everything that backs it."""
    stack = build_stack()
    fiche = add_fiche(stack)

    answer = recall(stack, client_id=CLIENT_A, problem=PROBLEM)

    assert not is_refusal(answer)
    assert [view.fiche.fiche_id for view in answer.fiches] == [fiche.fiche_id]
    view = answer.fiches[0]
    assert view.fiche.level is Level.VALIDATED
    assert view.success_rate == 0.5
    rendered = fiche_view(view.fiche, stack)
    assert rendered["niveau"] == "2"
    assert rendered["auteurType"] in {"humain", "agent", "mixte"}
    assert rendered["sources"] == ["guichet:ticket-2041"]


def test_recall_carries_a_usage_id():
    """EF-RET-01: every consultation gets an identifier, and it is not empty."""
    stack = build_stack()
    add_fiche(stack)

    answer = recall(stack, client_id=CLIENT_A, problem=PROBLEM)

    assert answer.usage_id.startswith("u-")
    assert stack.catalog.get_usage(answer.usage_id) is not None


def test_recall_answers_within_the_token_budget():
    """ENF-PERF-03: the answer fits in 1500 estimated tokens."""
    stack = build_stack()
    for number in range(6):
        add_fiche(stack, title=f"Connexions à la base épuisées, cas {number}")

    answer = recall(stack, client_id=CLIENT_A, problem=PROBLEM)

    assert answer.estimated_tokens <= stack.settings.max_recall_tokens
    assert len(answer.fiches) <= stack.settings.max_fiches
    assert len(answer.episodes) <= stack.settings.max_episodes


def test_recall_puts_the_perimeter_before_the_common():
    """EF-RES-04: the perimeter of the client comes first, the common after."""
    stack = build_stack()
    own = add_fiche(stack)
    shared = add_fiche(
        stack,
        scope=Scope(kind=ScopeKind.COMMON),
        title="Base saturée : vérifier les connexions avant la taille",
    )

    answer = recall(stack, client_id=CLIENT_A, problem=PROBLEM)

    assert [view.fiche.fiche_id for view in answer.fiches] == [own.fiche_id]
    assert [view.fiche.fiche_id for view in answer.common] == [shared.fiche_id]


def test_recall_never_serves_an_unvalidated_content_to_an_autonomous_agent():
    """EF-RES-05: an autonomous agent reads the validated, and is told the rest exists."""
    stack = build_stack()
    add_fiche(stack, level=Level.VALIDATED)
    add_fiche(stack, level=Level.PROPOSED, title="Base saturée, un cas encore à relire")
    agent = stack.principal(PrincipalRole.AGENT, CLIENT_A, AgentMode.AUTONOMOUS)

    answer = recall(stack, client_id=CLIENT_A, problem=PROBLEM, principal=agent)

    assert not is_refusal(answer)
    assert all(view.fiche.level is not Level.PROPOSED for view in answer.fiches)
    assert answer.episodes == ()
    assert "non validés" in answer.not_served_note


def test_recall_all_is_refused_to_an_autonomous_agent():
    """R8: the unvalidated content never reaches an autonomous agent."""
    stack = build_stack()
    agent = stack.principal(PrincipalRole.AGENT, CLIENT_A, AgentMode.AUTONOMOUS)

    answer = recall_all(stack, client_id=CLIENT_A, problem=PROBLEM, principal=agent)

    assert is_refusal(answer)
    assert answer["error"] == "policy_denied"
    assert "D.2" in answer["message"]


def test_recall_of_another_client_is_refused_before_the_tool_runs():
    """R9: the perimeter is read in the token, and the refusal is in the journal."""
    stack = build_stack()
    add_fiche(stack)
    agent = stack.principal(PrincipalRole.AGENT, CLIENT_B, AgentMode.AUTONOMOUS)

    answer = recall(stack, client_id=CLIENT_A, problem=PROBLEM, principal=agent)

    assert is_refusal(answer)
    assert "S.1" in answer["message"]
    refusals = stack.journal.query(tool="recall", decision=Decision.FORBID)
    assert refusals and refusals[0].scope == f"client-{CLIENT_A}"


def test_recall_all_adds_the_lessons_and_the_unvalidated_episodes():
    """Table 15.1: the complete view adds the lessons, signalled as such."""
    stack = build_stack()
    add_fiche(stack)
    lesson = add_lesson(stack)
    engineer = stack.principal(PrincipalRole.ENGINEER, CLIENT_A)

    answer = recall_all(stack, client_id=CLIENT_A, problem=PROBLEM, principal=engineer)

    assert not is_refusal(answer)
    assert [one.lesson_id for one in answer.lessons] == [lesson.lesson_id]
    assert lesson.level is Level.PROPOSED
    assert answer.episodes
    assert all(episode.level is not Level.VALIDATED for episode in answer.episodes)


def test_recall_serves_the_lesson_of_another_agent():
    """R12: a lesson learnt the day before by another agent is served."""
    stack = build_stack()
    lesson = add_lesson(
        stack,
        use_case="Rétablir un service qui épuise les connexions à sa base",
        advice=("Comparer le groupe de connexions avant la taille de la base",),
    )
    agent = stack.principal(PrincipalRole.AGENT, CLIENT_A, AgentMode.ASSISTED)

    answer = recall_all(stack, client_id=CLIENT_A, problem=PROBLEM, principal=agent)

    assert not is_refusal(answer)
    assert lesson.lesson_id in [one.lesson_id for one in answer.lessons]
    assert "Comparer le groupe de connexions" in answer.avoid or answer.lessons


def test_recall_of_an_item_out_of_the_perimeter_is_refused():
    """The tool checks the perimeter of the object it returns (10.4)."""
    stack = build_stack()
    fiche = add_fiche(stack, scope=Scope(kind=ScopeKind.CLIENT, client_id=CLIENT_B))
    engineer = stack.principal(PrincipalRole.ENGINEER, CLIENT_A)

    from sillage.tools import get_item

    answer = get_item(stack, id=fiche.fiche_id, principal=engineer)

    assert is_refusal(answer)
    assert answer["error"] == "validation_error"


def test_recall_degrades_when_the_memory_is_down():
    """ENF-DISP-02, R21: a memory that is down does not break the caller."""
    stack = build_stack()

    class Broken:
        def retrieve(self, **_kwargs):
            raise BackendUnavailable("la mémoire ne répond pas")

    broken = stack.replace(memory=Broken())

    answer = recall(broken, client_id=CLIENT_A, problem=PROBLEM)

    assert not is_refusal(answer)
    assert answer.fiches == ()
    assert "ne répond pas" in answer.not_served_note


def test_the_tools_answer_on_the_stack_of_the_demonstration():
    """The stack of the demonstration is the stack the tools expect: the smoke relies on it."""
    from sillage.demo.stack import build

    stack = build()

    answer = recall(stack, client_id=CLIENT_A, problem=PROBLEM)

    assert answer.usage_id
    assert len(answer.fiches) <= 3
    assert answer.avoid
    assert answer.estimated_tokens <= stack.settings.max_recall_tokens
