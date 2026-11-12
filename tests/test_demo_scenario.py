"""The narrated demonstration: it runs, it tells the story, it shows a refusal.

The scenario is what the three minute video is recorded from, so the tests check
what a judge sees on screen: the six journeys of chapter 8, one readable line per
step, and - the most important - a **visible refusal**, with the rule that
refused.

The catalogue must also be left coherent: the demonstration may add episodes and
fiche states, but it may not break the isolation between the perimeters or leave
a fiche without a source.
"""

from __future__ import annotations

import pytest

from sillage.demo.scenario import run
from sillage.demo.stack import build
from sillage.guards.sources import is_publishable
from sillage.kernel.config import Settings
from sillage.kernel.types import Level


@pytest.fixture(scope="module")
def scenario_lines() -> list[str]:
    """The demonstration, played once for the whole module."""
    return run(build(settings=Settings(offline=True)))


def test_the_scenario_runs_end_to_end(scenario_lines: list[str]) -> None:
    """The scenario plays from the first line to the last."""
    lines = scenario_lines
    assert lines
    assert all(isinstance(line, str) for line in lines)
    assert any(line.strip() for line in lines)
    assert len([line for line in lines if line.strip()]) > 20


def test_the_scenario_tells_the_six_journeys(scenario_lines: list[str]) -> None:
    """Every journey of chapter 8 has its place on screen."""
    printed = "\n".join(scenario_lines)
    for journey in ("8.1", "8.2", "8.3", "8.4", "8.5", "8.6"):
        assert journey in printed, journey
    assert "Piste qui a échoué" in printed
    assert "Piste qui a marché" in printed
    assert "à l'écart" in printed
    assert "référent est prévenu" in printed


def test_the_scenario_shows_a_policy_refusal(scenario_lines: list[str]) -> None:
    """The refusal is visible on screen, with the rule that refused."""
    printed = "\n".join(scenario_lines)
    assert "refus" in printed
    assert "S.1" in printed, "le refus entre clients doit nommer sa règle"


def test_the_scenario_shows_what_the_memory_served(scenario_lines: list[str]) -> None:
    """The served fiche, its level and its rate are read from the stack."""
    printed = "\n".join(scenario_lines)
    assert "Fiche servie" in printed
    assert "taux de réussite" in printed
    assert "recall renvoie" in printed


def test_the_scenario_is_reproducible(settings: Settings) -> None:
    """Two runs tell the same story: the demo is deterministic."""
    first = run(build(settings=settings))
    second = run(build(settings=settings))
    assert first == second


def test_the_catalogue_is_coherent_after_the_scenario(settings: Settings) -> None:
    """The demonstration leaves a catalogue the portal can read."""
    stack = build(settings=settings)
    run(stack)
    for episode in stack.catalog.list_episodes(limit=0):
        assert stack.catalog.get_trace(episode.work_item_id) is not None, episode.episode_id
        assert episode.scope.actor_id in {"client-a", "client-b", "interne"}
    for fiche in stack.catalog.list_fiches():
        assert is_publishable(fiche), fiche.fiche_id
        assert fiche.level in (
            Level.VALIDATED,
            Level.PROVEN,
            Level.SET_ASIDE,
        ), fiche.level


def test_a_fiche_set_aside_is_not_served_to_an_agent(settings: Settings) -> None:
    """A fiche that failed twice leaves the agents, and the humans keep it."""
    from sillage.demo.lookup import consult
    from sillage.kernel.types import AgentMode, PrincipalRole

    stack = build(settings=settings)
    run(stack)
    aside = [fiche for fiche in stack.catalog.list_fiches() if fiche.level is Level.SET_ASIDE]
    assert aside, "la démonstration doit mettre une fiche à l'écart"
    agent = stack.principal(PrincipalRole.AGENT, "a", AgentMode.AUTONOMOUS)
    found = consult(stack, principal=agent, problem=aside[0].title, limit=20)
    assert all(item["id"] != aside[0].fiche_id for item in found.fiches)
    assert stack.catalog.get_fiche(aside[0].fiche_id) is not None
    humans = stack.principal(PrincipalRole.ENGINEER, "a")
    seen = consult(stack, principal=humans, problem=aside[0].title, limit=20)
    assert any(item["id"] == aside[0].fiche_id for item in seen.fiches)
