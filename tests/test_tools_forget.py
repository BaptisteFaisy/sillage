"""The forgetting of one work (EF-ENT-03, R19).

The cascade of a single work item: the work goes, its episode goes, the lessons
built on it go, and the fiches that relied on it are **flagged**, never deleted
- the decision belongs to a referent (EF-ENT-02).

The tests also check who may forget: an administrator, and the engineer who did
the work - and nobody else.
"""

from __future__ import annotations

from dataclasses import replace

from sillage.capture import sample
from sillage.kernel.ids import pseudonym
from sillage.kernel.protocols import Principal
from sillage.kernel.types import PrincipalRole, Scope, ScopeKind
from sillage.tools import forget
from sillage.tools.errors import is_refusal
from tools_fakes import CLIENT_A, add_fiche, build_stack

#: The work of the demonstration ticket.
WORK_ITEM = "guichet:ticket-2041"


def test_forgetting_a_work_removes_its_episode_and_flags_the_dependent_fiche():
    """R19: the episode goes, the fiche that used it is kept and signalled."""
    stack = build_stack()
    fiche = add_fiche(stack, sources=(WORK_ITEM,))
    admin = stack.principal(PrincipalRole.ADMIN)

    answer = forget(stack, work_item_id=WORK_ITEM, principal=admin)

    assert not is_refusal(answer)
    assert answer["episodeId"]
    assert answer["supprime"]["episodes"] == 1
    assert fiche.fiche_id in answer["fichesSignalees"]
    episodes = tuple(stack.catalog.list_episodes(scope=Scope(kind=ScopeKind.CLIENT, client_id=CLIENT_A)))
    assert not [one for one in episodes if one.work_item_id == WORK_ITEM]
    assert stack.catalog.get_fiche(fiche.fiche_id) is not None
    assert fiche.fiche_id in [one.fiche_id for one in stack.catalog.flagged_fiches()]


def test_the_memory_of_the_work_is_erased():
    """EF-ENT-03: the episode leaves the memory, not only the catalogue."""
    stack = build_stack()
    admin = stack.principal(PrincipalRole.ADMIN)
    before = len(tuple(stack.memory.records()))

    forget(stack, work_item_id=WORK_ITEM, principal=admin)

    assert len(tuple(stack.memory.records())) < before


def test_only_the_administrator_or_the_author_may_forget_a_work():
    """Table 10.5: an engineer forgets their own work, and nobody else's."""
    stack = build_stack()
    engineer = stack.principal(PrincipalRole.ENGINEER, CLIENT_A)
    other = Principal(subject="p-autre-ingenieur", role=PrincipalRole.ENGINEER, client_id=CLIENT_A)
    trace = replace(sample.ticket_2041(), author=pseudonym(other.subject))
    stack.ingest.ingest_trace(trace)

    refused = forget(stack, work_item_id=WORK_ITEM, principal=engineer)

    assert is_refusal(refused)
    assert "M.5" in refused["message"]


def test_the_author_of_a_work_may_forget_it():
    """The engine compares the pseudonym of the author with the one of the caller."""
    stack = build_stack()
    engineer = stack.principal(PrincipalRole.ENGINEER, CLIENT_A)
    trace = replace(sample.ticket_2041(), author=pseudonym(engineer.subject))
    stack.ingest.ingest_trace(trace)

    answer = forget(stack, work_item_id=WORK_ITEM, principal=engineer)

    assert not is_refusal(answer)
    assert answer["supprime"]["episodes"] == 1


def test_forgetting_an_unknown_work_is_an_error_and_not_an_exception():
    stack = build_stack()
    admin = stack.principal(PrincipalRole.ADMIN)

    answer = forget(stack, work_item_id="guichet:inconnu", principal=admin)

    assert is_refusal(answer)
    assert answer["error"] == "not_found"
