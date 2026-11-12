"""What a knowledge object looks like on the wire (annexe E).

The tools answer with typed objects, and the gateway renders them: this module
is the single place that turns a fiche, an episode or a lesson into the JSON a
client reads. The shape is the one of annexe E of the specification, in
French, because the clients of the demonstration are French.

Every figure shown here is read from the catalogue, computed by
:mod:`sillage.catalog.scoring` - the success rate, the freshness, the level.
No model writes a number (ENF-IA-01), and no figure is grouped by person
(ENF-RH-01): the author is a type, never a name.
"""

from __future__ import annotations

from typing import Any

from sillage.catalog.scoring import freshness_days, success_rate
from sillage.kernel.types import LEVEL_CODE, Episode, Fiche, Lesson, Level, PrincipalRole

#: The level of a knowledge object, in the words of the specification.
LEVEL_NAMES: dict[Level, str] = {
    Level.RAW: "épisode brut",
    Level.PROPOSED: "proposée",
    Level.VALIDATED: "validée",
    Level.PROVEN: "éprouvée",
    Level.SET_ASIDE: "mise à l'écart",
    Level.WITHDRAWN: "retirée",
}

#: The role of a caller, in the words of the specification.
ROLE_NAMES: dict[PrincipalRole, str] = {
    PrincipalRole.ENGINEER: "ingénieur",
    PrincipalRole.REFERENT: "référent",
    PrincipalRole.AGENT: "agent",
    PrincipalRole.TEAM_LEAD: "chef d'équipe",
    PrincipalRole.ADMIN: "administrateur",
}

#: The type of an author, in the words of the specification.
AUTHOR_KINDS: dict[str, str] = {
    "human": "humain",
    "agent": "agent",
    "mixed": "mixte",
}


def fiche_view(fiche: Fiche, stack: Any) -> dict[str, Any]:
    """One fiche with everything that backs it: level, source, rate, freshness."""
    now = stack.now()
    rate = success_rate(successes=fiche.success_count, failures=fiche.failure_count)
    return {
        "id": fiche.fiche_id,
        "type": "fiche",
        "niveau": LEVEL_CODE[fiche.level],
        "niveauNom": LEVEL_NAMES.get(fiche.level, fiche.level.value),
        "titre": fiche.title,
        "texte": fiche.body,
        "etapes": list(fiche.steps),
        "aEviter": list(fiche.avoid),
        "validite": fiche.validity,
        "sources": list(fiche.sources),
        "origine": fiche.provenance,
        "auteurType": author_kind_of(fiche, stack),
        "tauxReussite": _rate_text(fiche),
        "tauxReussiteValeur": rate,
        "derniereConfirmation": _date_of(fiche, freshness_days(fiche, now)),
        "miseEcart": fiche.set_aside_reason,
        "perimetre": fiche.scope.actor_id,
    }


def episode_view(episode: Episode) -> dict[str, Any]:
    """One episode: the situation, the intention, the steps and the outcome."""
    return {
        "id": episode.episode_id,
        "type": "episode",
        "niveau": LEVEL_CODE[episode.level],
        "niveauNom": LEVEL_NAMES.get(episode.level, episode.level.value),
        "situation": episode.situation,
        "intention": episode.intent,
        "etapes": [turn.action for turn in episode.turns],
        "issue": episode.outcome.value,
        "auteurType": AUTHOR_KINDS.get(episode.author_kind.value, episode.author_kind.value),
        "source": episode.source.value,
        "pistesEchouees": list(episode.failed_paths),
        "diagnosticRejete": episode.rejected_diagnosis,
        "corrigePar": episode.corrected_by,
        "travail": episode.work_item_id,
        "perimetre": episode.scope.actor_id,
    }


def lesson_view(lesson: Lesson) -> dict[str, Any]:
    """One lesson: its use case, its advice and its traps."""
    return {
        "id": lesson.lesson_id,
        "type": "lecon",
        "niveau": LEVEL_CODE[lesson.level],
        "niveauNom": LEVEL_NAMES.get(lesson.level, lesson.level.value),
        "casUsage": lesson.use_case,
        "conseils": list(lesson.advice),
        "pieges": list(lesson.pitfalls),
        "confiance": lesson.confidence,
        "episodes": list(lesson.episode_ids),
        "perimetre": lesson.scope.actor_id,
    }


def recall_view(result: Any, stack: Any) -> dict[str, Any]:
    """The answer of a consultation, in the shape of annexe E.

    The name of the note about what was not served is the one of the annexe:
    ``episodesNonServis``. It says what was withheld, never why it was hidden
    from this caller - the rules are in the journal.
    """
    return {
        "usageId": result.usage_id,
        "intention": result.intent,
        "fiches": [fiche_view(view.fiche, stack) for view in result.fiches],
        "commun": [fiche_view(view.fiche, stack) for view in result.common],
        "aEviter": list(result.avoid),
        "episodes": [episode_view(episode) for episode in result.episodes],
        "lecons": [lesson_view(lesson) for lesson in result.lessons],
        "episodesNonServis": result.not_served_note,
        "jetonsEstimes": result.estimated_tokens,
        "perimetre": result.scope.actor_id if result.scope is not None else "",
    }


def author_kind_of(fiche: Fiche, stack: Any) -> str:
    """The type of author of a fiche, read from the episodes it cites (EF-RES-03).

    The catalogue knows who worked on the sources; the tool only says whether
    the work was done by a human, by an agent, or by both - never by whom.
    """
    kinds = {
        AUTHOR_KINDS.get(episode.author_kind.value, episode.author_kind.value)
        for episode in stack.catalog.list_episodes()
        if episode.work_item_id in fiche.sources
    }
    if not kinds:
        return ""
    if len(kinds) > 1:
        return "mixte"
    return kinds.pop()


def _rate_text(fiche: Fiche) -> str:
    """The success rate as the annexe E shows it: the counts, not a percentage."""
    return f"{fiche.success_count} réussite(s), {fiche.failure_count} échec(s)"


def _date_of(fiche: Fiche, days: int | None) -> str:
    """The date of the last confirmation, in French, or a dash when there is none."""
    if fiche.last_confirmation is None or days is None:
        return "—"
    return fiche.last_confirmation.strftime("%d/%m/%Y")


__all__ = [
    "LEVEL_NAMES",
    "ROLE_NAMES",
    "author_kind_of",
    "episode_view",
    "fiche_view",
    "lesson_view",
    "recall_view",
]
