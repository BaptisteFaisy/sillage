"""Distillation of a trace into an episode, and reflection into lessons.

Two different jobs, in the order of chapter 4 of the specification:

* the **distillation** turns one finished job into an episode: the situation,
  the intention, the story told turn by turn, the verdict, the justification, the
  teachings - and, kept apart, the paths that were tried without success
  (EF-DIS-03, EF-DIS-04);
* the **reflection** turns several close episodes of **one perimeter** into a
  lesson: what they teach, the traps they show, a confidence computed by code.

The isolation rule is the point of this module (EF-DIS-02): a reflection never
crosses two clients. It is not a convention here, it is an assertion - and the
spaces themselves are disjoint, so a lesson of the common knowledge cannot lead
back to the episode it came from (EF-GEN-07, R15).

A model may write **text only**. The verdict of a job, the confidence of a lesson
and every other figure are computed by code (ENF-IA-01): the JSON the model
returns carries no number, and a number it would return is ignored.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from sillage.capture.episode import FAILURE_WORDS, intent_of, outcome_of, situation_of, turns_of
from sillage.kernel.ids import episode_id, lesson_id
from sillage.kernel.protocols import MemoryRecord, ModelBackend
from sillage.kernel.types import Episode, Lesson, Level, ModelTier, Outcome, Scope, Trace, Turn
from sillage.memory.grouping import (
    Item,
    cluster,
    episodes_of,
    item_of_episode,
    item_of_record,
    records_of,
)
from sillage.memory.local_records import episode_metadata, split_list

#: Words that tell an action did not work: the failed paths of an episode.
FAILED_PATH_HINTS = (
    "ne règle rien",
    "ne change rien",
    "sans effet",
    "aucun effet",
    "sans résultat",
    "inutile",
    "inefficace",
)

#: How many close episodes are needed before a lesson is proposed.
MIN_EPISODES_FOR_A_LESSON = 2

#: The shape the model must answer with: text, and never a number.
_DISTIL_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "situation": {"type": "string"},
        "intention": {"type": "string"},
        "justification": {"type": "string"},
        "enseignements": {"type": "array", "items": {"type": "string"}},
        "pistes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["situation", "intention", "enseignements", "pistes"],
}

#: The shape the model must answer with for a reflection: text only, again.
_REFLECT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "casUsage": {"type": "string"},
        "conseils": {"type": "array", "items": {"type": "string"}},
        "pieges": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["casUsage", "conseils", "pieges"],
}


# -- the episode ------------------------------------------------------------ #
def episode_content(trace: Trace) -> str:
    """The French text of an episode, as the offline strategy distils it.

    It is the text the search reads: the situation, the intention, the story turn
    by turn, the verdict, the teachings, and - on its own line - the paths that
    were tried without success.
    """
    parts = [f"Situation : {situation_of(trace)}", f"Intention : {intent_of(trace)}"]
    turns = turns_of(trace)
    if turns:
        parts.append("Déroulé :")
        parts.extend(_turn_line(turn) for turn in turns)
    parts.append(f"Verdict : {outcome_of(trace).value}")
    teachings = teachings_of(trace)
    if teachings:
        parts.append("Enseignements : " + " ; ".join(teachings))
    paths = failed_paths_of(trace)
    if paths:
        parts.append("Pistes essayées sans succès : " + " ; ".join(paths))
    return "\n".join(parts)


def episode_metadata_of(trace: Trace, *, token: str = "") -> dict[str, str]:
    """The metadata of the record of one episode, every field read by the code."""
    return episode_metadata(
        trace,
        situation=situation_of(trace),
        intent=intent_of(trace),
        outcome=outcome_of(trace).value,
        teachings=teachings_of(trace),
        pitfalls=failed_paths_of(trace),
        token=token,
    )


def teachings_of(trace: Trace) -> tuple[str, ...]:
    """What the successful turns of the job teach, read by the code."""
    paths = set(failed_paths_of(trace))
    found: list[str] = []
    for turn in turns_of(trace):
        if turn.result and turn.action not in paths:
            found.append(turn.action)
    return tuple(found[:3])


def failed_paths_of(trace: Trace) -> tuple[str, ...]:
    """The paths that were tried without success, kept apart (EF-DIS-04)."""
    found: list[str] = []
    for turn in turns_of(trace):
        if not (_is_failure(turn.action) or _is_failure(turn.result)):
            continue
        line = turn.action if not turn.result else f"{turn.action} -> {turn.result}"
        if line not in found:
            found.append(line)
    return tuple(found)


def distil_episode(
    trace: Trace,
    records: Sequence[MemoryRecord] = (),
    model: ModelBackend | None = None,
) -> Episode:
    """The episode of one finished job (EF-DIS-01, EF-DIS-05).

    The model, when there is one, rewrites the situation, the intention, the
    justification and the teachings in the vocabulary of the operations. The
    verdict, the turns and the identity of the episode are never written by a
    model: the code reads them from the trace (ENF-IA-01). Redistilling the same
    trace gives the same episode id, so a corrected trace replaces its episode
    instead of adding a second one (EF-DIS-07).
    """
    turns = turns_of(trace)
    outcome = outcome_of(trace)
    draft = _ask_model(model, trace, turns)
    return Episode(
        episode_id=episode_id(trace.scope.actor_id, trace.work_item_id),
        scope=trace.scope,
        work_item_id=trace.work_item_id,
        situation=_text_of(draft.get("situation"), situation_of(trace)),
        intent=_text_of(draft.get("intention"), intent_of(trace)),
        turns=turns,
        outcome=outcome,
        justification=_text_of(draft.get("justification"), _justification(trace, outcome)),
        teachings=_merge(teachings_of(trace), draft.get("enseignements")),
        failed_paths=_merge(failed_paths_of(trace), draft.get("pistes")),
        memory_record_id=_record_id_of(records, trace),
        source=trace.source,
        author_kind=trace.author_kind,
        author=trace.author,
        technologies=trace.technologies,
        level=Level.RAW,
        created_at=trace.closed_at,
        rejected_diagnosis=trace.origin.get("diagnosticRejete", ""),
        corrected_by=trace.origin.get("verdictPar", ""),
    )


# -- the reflection --------------------------------------------------------- #
def reflect(
    scope: Scope,
    episodes: Sequence[Episode],
    model: ModelBackend | None = None,
) -> tuple[Lesson, ...]:
    """The lessons several close episodes of **one perimeter** teach (R5).

    The assertion at the door is the isolation rule of EF-DIS-02: an episode of
    another client in the batch stops the reflection, it does not leak into it.
    """
    _assert_same_scope(scope, episodes)
    lessons: list[Lesson] = []
    for group in cluster([item_of_episode(episode) for episode in episodes]):
        if len(group) >= MIN_EPISODES_FOR_A_LESSON:
            lessons.append(_lesson_from_episodes(scope, group, model))
    return tuple(lessons)


def lesson_from_records(scope: Scope, records: Sequence[MemoryRecord]) -> tuple[Lesson, ...]:
    """The lessons the offline service draws from the records of one perimeter.

    Same rule as :func:`reflect`: a record of another perimeter is refused before
    the grouping, never mixed into a lesson.
    """
    _assert_records_scope(scope, records)
    lessons: list[Lesson] = []
    for group in cluster([item_of_record(record) for record in records]):
        if len(group) >= MIN_EPISODES_FOR_A_LESSON:
            lessons.append(_lesson_from_records(scope, group))
    return tuple(lessons)


def confidence_of(count: int, successes: int) -> float:
    """The generalisation confidence of a lesson, computed by code (ENF-IA-01).

    It reads 0.1 for nothing at all, and grows with the number of episodes and
    the share of them that succeeded. Both contributions saturate - an episode
    more adds less than the previous one - so a lesson never reaches 1.0: it
    stays a proposal, at 0.9 at the most, until a referent has read it and a
    fiche has been proven by usage.
    """
    if count <= 0:
        return 0.1
    breadth = 0.4 * (count - 1) / count
    outcome = 0.2 * (successes / count)
    return round(min(1.0, max(0.1, 0.3 + breadth + outcome)), 2)


# -- helpers ---------------------------------------------------------------- #
def _turn_line(turn: Turn) -> str:
    """One turn of the story, with the result it obtained."""
    if turn.result:
        return f"- {turn.action} -> {turn.result}"
    return f"- {turn.action}"


def _is_failure(text: str) -> bool:
    """True when a text says the attempt did not work."""
    lowered = (text or "").lower()
    return any(hint in lowered for hint in (*FAILURE_WORDS, *FAILED_PATH_HINTS))


def _justification(trace: Trace, outcome: Outcome) -> str:
    """Why the job is a success or a failure, read from the closing event."""
    given = trace.origin.get("raisonVerdict", "")
    if given:
        return given
    return f"Verdict {outcome.value} d'après l'événement de clôture du travail."


def _text_of(proposed: object, fallback: str) -> str:
    """The text a model proposed - and the code's own text when it proposed a number.

    A model writes text and nothing else (ENF-IA-01): a figure it would slip in a
    field is dropped, not stored.
    """
    if isinstance(proposed, str) and proposed.strip():
        return proposed.strip()
    return fallback


def _merge(code_values: Sequence[str], model_values: object) -> tuple[str, ...]:
    """The code values first, then the ones the model proposed, without repeats."""
    found = list(code_values)
    if isinstance(model_values, list):
        for value in model_values:
            text = str(value).strip()
            if text and text not in found:
                found.append(text)
    return tuple(found)


def _record_id_of(records: Sequence[MemoryRecord], trace: Trace) -> str | None:
    """The memory record that holds this episode, when there is one."""
    for record in records:
        if record.metadata.get("workItemId") == trace.work_item_id:
            return record.record_id
    return None


def _lesson_from_episodes(scope: Scope, group: Sequence[Item], model: ModelBackend | None) -> Lesson:
    """The lesson several close episodes teach."""
    episodes = episodes_of(group)
    draft = _ask_reflection_model(model, episodes)
    successes = sum(1 for episode in episodes if episode.outcome is Outcome.SUCCESS)
    return Lesson(
        lesson_id=lesson_id(scope.actor_id, *(episode.episode_id for episode in episodes)),
        scope=scope,
        use_case=_text_of(draft.get("casUsage"), _shared_intent(episodes)),
        advice=_merge(_shared(episodes, "teachings"), draft.get("conseils")),
        pitfalls=_merge(_shared(episodes, "failed_paths"), draft.get("pieges")),
        confidence=confidence_of(len(episodes), successes),
        episode_ids=tuple(episode.episode_id for episode in episodes),
        level=Level.PROPOSED,
        created_at=episodes[0].created_at if episodes else None,
    )


def _lesson_from_records(scope: Scope, group: Sequence[Item]) -> Lesson:
    """The lesson several close records teach, built by the code alone."""
    records = records_of(group)
    teachings: list[str] = []
    pitfalls: list[str] = []
    for record in records:
        teachings.extend(split_list(record.metadata.get("teachings", "")))
        pitfalls.extend(split_list(record.metadata.get("pitfalls", "")))
    successes = sum(1 for record in records if record.metadata.get("outcome") == "success")
    return Lesson(
        lesson_id=lesson_id(scope.actor_id, *(record.record_id for record in records)),
        scope=scope,
        use_case=_shared_intent_of_records(records),
        advice=tuple(dict.fromkeys(teachings)),
        pitfalls=tuple(dict.fromkeys(pitfalls)),
        confidence=confidence_of(len(records), successes),
        episode_ids=tuple(record.record_id for record in records),
        level=Level.PROPOSED,
        created_at=records[0].created_at if records else None,
    )


def _assert_same_scope(scope: Scope, episodes: Sequence[Episode]) -> None:
    """EF-DIS-02: a reflection never crosses two perimeters."""
    for episode in episodes:
        assert episode.scope.actor_id == scope.actor_id, (
            "EF-DIS-02 : une réflexion ne croise jamais deux périmètres "
            f"({episode.scope.actor_id} reçu dans {scope.actor_id})"
        )


def _assert_records_scope(scope: Scope, records: Sequence[MemoryRecord]) -> None:
    """EF-DIS-02, for the records: a record of another perimeter is refused."""
    for record in records:
        owner = record.metadata.get("scope", scope.actor_id)
        assert owner == scope.actor_id, (
            f"EF-DIS-02 : une réflexion ne croise jamais deux périmètres ({owner} reçu dans {scope.actor_id})"
        )


def _shared_intent(episodes: Sequence[Episode]) -> str:
    """The intention the episodes of a lesson share."""
    for episode in episodes:
        if episode.intent:
            return episode.intent
    return ""


def _shared_intent_of_records(records: Sequence[MemoryRecord]) -> str:
    """The intention the records of a lesson share."""
    for record in records:
        intent = record.metadata.get("intent", "")
        if intent:
            return intent
    return ""


def _shared(episodes: Sequence[Episode], attribute: str) -> tuple[str, ...]:
    """The teachings or the failed paths of the episodes of a lesson."""
    found: list[str] = []
    for episode in episodes:
        for value in getattr(episode, attribute):
            if value not in found:
                found.append(value)
    return tuple(found)


def _ask_model(model: ModelBackend | None, trace: Trace, turns: Sequence[Turn]) -> dict[str, object]:
    """What the model proposes about a job, as plain text. Never a number."""
    if model is None:
        return {}
    return _complete(
        model,
        prompt=_distil_prompt(trace, turns),
        system=(
            "Tu résumes un travail d'exploitation en français. "
            "Réponds uniquement par du texte, jamais par un chiffre."
        ),
        schema=_DISTIL_SCHEMA,
    )


def _ask_reflection_model(model: ModelBackend | None, episodes: Sequence[Episode]) -> dict[str, object]:
    """What the model proposes about a set of episodes: text only."""
    if model is None:
        return {}
    prompt = "\n".join(
        [
            "Voici des épisodes d'un même périmètre.",
            *(f"Épisode {episode.episode_id} : {episode.intent}" for episode in episodes),
            "Rédige le cas d'usage, les conseils et les pièges.",
        ]
    )
    return _complete(
        model,
        prompt=prompt,
        system="Tu écris une leçon en français. Jamais de chiffre.",
        schema=_REFLECT_SCHEMA,
    )


def _complete(
    model: ModelBackend, *, prompt: str, system: str, schema: dict[str, object]
) -> dict[str, object]:
    """One model call, read as JSON, and nothing at all when it fails."""
    try:
        answer = model.complete(prompt=prompt, system=system, tier=ModelTier.CAPABLE, json_schema=schema)
    except Exception:  # a model that fails must not fail the distillation
        return {}
    return _read_json(answer)


def _distil_prompt(trace: Trace, turns: Sequence[Turn]) -> str:
    """The prompt of the distillation: the trace and nothing else."""
    lines = [
        f"Travail : {trace.work_item_id}",
        f"Technologies : {', '.join(trace.technologies)}",
        f"Situation : {situation_of(trace)}",
        f"Intention : {intent_of(trace)}",
    ]
    lines.extend(f"Étape : {turn.action} | résultat : {turn.result}" for turn in turns)
    return "\n".join(lines)


def _read_json(answer: str) -> dict[str, object]:
    """Read the JSON of a model, or nothing when the answer is not JSON."""
    text = (answer or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = [
    "FAILED_PATH_HINTS",
    "MIN_EPISODES_FOR_A_LESSON",
    "confidence_of",
    "distil_episode",
    "episode_content",
    "episode_metadata_of",
    "failed_paths_of",
    "lesson_from_records",
    "reflect",
    "teachings_of",
]
