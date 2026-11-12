"""The narrated demonstration: the six journeys, one readable line per step.

``run()`` returns the lines of the demonstration in the order they are shown on
screen during the three minute video (specification 19.2). Every line is read
from the stack the code built - the episode that appeared, the fiche that was
served, the rule that refused - so the demonstration cannot claim something the
memory does not hold.

The scenes live in :mod:`sillage.demo.scenes`; this module only orders them and
closes on what the run produced. One scene per journey keeps every file and
every function small, and lets a lot add a scene without touching the others.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from sillage.demo import scenes
from sillage.demo.stack import Stack, build

if TYPE_CHECKING:  # the stack is only read here, never built
    pass

#: The scenes of the demonstration, in the order of the video.
SCENES: tuple[Callable[[Stack], list[str]], ...] = (
    scenes.scene_engineer_closes_a_ticket,
    scenes.scene_agent_investigates,
    scenes.scene_developer_and_code_agent,
    scenes.scene_human_corrects_an_agent,
    scenes.scene_common_knowledge,
    scenes.scene_fiche_set_aside,
    scenes.scene_skill_export,
)


def run(stack: Stack | None = None) -> list[str]:
    """Play the demonstration and return its lines.

    Without a stack, one is built and seeded: the demo has to show something,
    so the history, the lessons and the fiches are loaded first.
    """
    built = stack if stack is not None else build()
    lines = ["Sillage — une mémoire partagée pour les ingénieurs, les développeurs et les agents.", ""]
    for scene in SCENES:
        lines.extend(scene(built))
        lines.append("")
    lines.extend(_closing(built))
    return lines


def _closing(stack: Stack) -> list[str]:
    """The last lines: what the run left in the catalogue."""
    episodes = len(tuple(stack.catalog.list_episodes(limit=0)))
    lessons = len(tuple(stack.catalog.list_lessons()))
    fiches = tuple(stack.catalog.list_fiches())
    proven = sum(1 for fiche in fiches if fiche.level.value == "proven")
    common = sum(1 for lesson in stack.catalog.list_lessons() if lesson.scope.is_common)
    return [
        "Ce que cette démonstration vient de montrer :",
        f"- {episodes} épisodes capturés, {lessons} leçons, {len(fiches)} fiches.",
        f"- {proven} fiche(s) éprouvée(s) par l'usage, {common} leçon(s) dans le commun.",
        "- Le cloisonnement entre clients tient : il est tenu par des règles, avant tout appel d'outil.",
        "- Aucun chiffre n'a été écrit par un modèle, aucun indicateur ne parle d'une personne.",
    ]


__all__ = ["SCENES", "run"]
