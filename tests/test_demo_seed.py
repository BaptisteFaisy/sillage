"""The seed of the demo: what it loads, and what it does without ``data/``.

The seed must always leave the stack in a state the demonstration can show: a
history of finished jobs, the lessons drawn from it, the fiches a referent
validated, and one common lesson. The tests below check the three properties
that matter:

* the counters describe the **state** of the stack, and two seeds give the same
  numbers - the demo is reproducible;
* **an empty ``data/`` directory is a normal state**: the deterministic fallback
  answers, with the eight fault families of specification 19.1;
* the seed is **idempotent**: loading the same history twice changes nothing
  (EF-CAP-11, R3).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sillage.demo.history import FAMILIES
from sillage.demo.history_file import FALLBACK_SOURCE
from sillage.demo.seed import load_history, seed_stack
from sillage.demo.stack import Stack, build
from sillage.kernel.config import Settings
from sillage.kernel.types import Level, Scope, ScopeKind


def test_the_seed_loads_a_whole_history(settings: Settings) -> None:
    """The seed fills the stack with episodes, lessons and fiches."""
    stack = build(seed=False, settings=settings)
    counters = seed_stack(stack)
    assert counters["episodes"] >= 60, counters
    assert counters["lessons"] > 0
    assert counters["fiches"] > 0
    assert counters["clients"] == 2
    assert counters["perimeters"] == 3
    assert counters["common_lessons"] == 1
    assert counters["proven"] >= 1
    assert counters["validated"] >= 1


def test_the_seed_is_idempotent(settings: Settings) -> None:
    """Two seeds leave the same state: nothing is counted twice."""
    stack = build(seed=False, settings=settings)
    first = seed_stack(stack)
    second = seed_stack(stack)
    for key in ("traces", "episodes", "lessons", "fiches", "proven", "validated", "records"):
        assert first[key] == second[key], key
    assert second["loaded"] == 0, "le second chargement ne doit rien charger de plus"


def test_loading_the_history_twice_changes_nothing(settings: Settings) -> None:
    """The same trace received twice gives one episode (R3)."""
    stack = build(seed=False, settings=settings)
    first = load_history(stack)
    second = load_history(stack)
    assert first > 0
    assert second == 0
    episodes = len(tuple(stack.catalog.list_episodes(limit=0)))
    assert episodes == first


def test_the_fallback_answers_when_data_is_empty(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An empty ``data/`` directory is a normal state, not an error."""
    from sillage.demo import history_file

    monkeypatch.setattr(history_file, "HISTORY_PATH", tmp_path / "absent.json")
    stack = build(seed=False, settings=settings)
    counters = seed_stack(stack)
    assert counters["history_source"] == FALLBACK_SOURCE
    assert counters["families"] == len(FAMILIES) == 8
    assert counters["episodes"] >= 60


def test_a_broken_history_file_falls_back_too(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A file that cannot be parsed degrades to the built-in history."""
    from sillage.demo import history_file

    broken = tmp_path / "history.json"
    broken.write_text("{ ceci n'est pas du json", encoding="utf-8")
    monkeypatch.setattr(history_file, "HISTORY_PATH", broken)
    stack = build(seed=False, settings=settings)
    counters = seed_stack(stack)
    assert counters["history_source"] == FALLBACK_SOURCE
    assert counters["episodes"] > 0


def test_a_generated_history_file_is_read(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the ``data`` lot has produced its file, it is the one that is read."""
    from sillage.capture.clock import DEMO_MOMENT
    from sillage.demo import history_file
    from sillage.demo.history import ticket_payload

    payload = ticket_payload(
        FAMILIES[0], number=4242, perimeter="a", moment=DEMO_MOMENT, identity="ingenieur"
    )
    generated = tmp_path / "history.json"
    generated.write_text(json.dumps([payload]), encoding="utf-8")
    monkeypatch.setattr(history_file, "HISTORY_PATH", generated)
    stack = build(seed=False, settings=settings)
    counters = seed_stack(stack)
    assert counters["history_source"] == str(generated)
    assert stack.catalog.get_trace("guichet:ticket-4242") is not None


@pytest.fixture(scope="module")
def seeded_stack() -> Stack:
    """One seeded stack, shared by the tests that only read it."""
    stack = build(settings=Settings(offline=True))
    seed_stack(stack)
    return stack


def test_the_two_clients_and_the_internal_perimeter_are_seeded(seeded_stack: Stack) -> None:
    """Each client and the MSP itself hold their own episodes (EF-DIS-02)."""
    stack = seeded_stack
    for scope in stack.perimeters():
        episodes = tuple(stack.catalog.list_episodes(scope=scope, limit=0))
        assert episodes, scope.actor_id
        for episode in episodes:
            assert episode.scope.actor_id == scope.actor_id
    common = tuple(stack.catalog.list_lessons(scope=Scope(kind=ScopeKind.COMMON)))
    assert common, "le commun doit contenir la leçon généralisée"
    assert common[0].episode_ids == (), "une leçon commune ne renvoie à aucun épisode"


def test_every_fiche_cites_its_episodes(seeded_stack: Stack) -> None:
    """A fiche without a source is not publishable (EF-VAL-02)."""
    from sillage.guards.sources import is_publishable

    for fiche in seeded_stack.catalog.list_fiches():
        assert is_publishable(fiche), fiche.fiche_id
        assert fiche.claims
        assert fiche.level in (Level.VALIDATED, Level.PROVEN)


def test_no_fiche_holds_a_client_identifier_in_the_common_one(seeded_stack: Stack) -> None:
    """The common knowledge names no client (EF-GEN-03, R15)."""
    common = tuple(seeded_stack.catalog.list_lessons(scope=Scope(kind=ScopeKind.COMMON)))
    for lesson in common:
        text = " ".join(lesson.advice) + " " + lesson.use_case
        assert "Client A" not in text
        assert "retail" not in text
        assert "logistics" not in text


def test_the_stack_of_the_seed_is_the_one_that_was_given(settings: Settings) -> None:
    """The seed fills the stack it is given, it does not build another one."""
    stack = build(seed=False, settings=settings)
    before = id(stack)
    seed_stack(stack)
    assert id(stack) == before
    assert isinstance(stack, Stack)
