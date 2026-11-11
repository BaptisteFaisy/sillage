"""Entity families of the offline catalogue: one mixin per group of chapter 14.

Each mixin implements the matching part of ``kernel.protocols.CatalogStore`` and
relies only on the dictionaries of :class:`sillage.catalog.local_state.CatalogState`.
Splitting them keeps every file and every class small, and lets the reader check
one family against the table of chapter 14 at a time.

Two invariants hold in every method here: an author is a pseudonym stored as a
plain attribute and never used as a key, so no method can return a per-person
figure (ENF-RH-01); and no figure is ever guessed - what is written is what the
caller computed (ENF-IA-01).
"""

from __future__ import annotations

from collections.abc import Sequence

from sillage.catalog.local_search import query_words, rank
from sillage.catalog.local_state import filter_scope, newest_first, oldest_first, row_key
from sillage.kernel.types import Client, Episode, Feedback, Fiche, Lesson, Level, Scope, Trace, Usage


class ClientRows:
    """Tenants of the MSP (chapter 14 ``Client``)."""

    def put_client(self, client: Client) -> None:
        """Create or replace a tenant."""
        self.clients[client.client_id] = client

    def get_client(self, client_id: str) -> Client | None:
        """One tenant, ``None`` when unknown."""
        return self.clients.get(client_id)

    def list_clients(self) -> Sequence[Client]:
        """Every tenant, sorted by identifier."""
        return tuple(self.clients[key] for key in sorted(self.clients))


class TraceRows:
    """``Travail`` rows and the dedupe index (chapter 14, EF-CAP-11)."""

    def put_trace(self, trace: Trace) -> None:
        """Store a finished job.

        The events are kept as they came: offline this *is* the raw copy, and the
        deployed stack puts the same trace in the object store while the events
        go to AgentCore Memory (chapter 12.4).
        """
        self.traces[trace.work_item_id] = trace

    def get_trace(self, work_item_id: str) -> Trace | None:
        """One finished job, ``None`` when unknown."""
        return self.traces.get(work_item_id)

    def trace_seen(self, fingerprint: str) -> bool:
        """True when this exact capture was already received (R3)."""
        return fingerprint in self.fingerprints

    def mark_trace_seen(self, fingerprint: str, work_item_id: str) -> None:
        """Remember a fingerprint and the work item it belongs to."""
        self.fingerprints[fingerprint] = work_item_id


class EpisodeRows:
    """Episodes: one per finished job (chapter 14 ``Episode``)."""

    def put_episode(self, episode: Episode) -> None:
        """Store an episode, replacing the previous row of the same id."""
        self.episodes[episode.episode_id] = episode

    def get_episode(self, episode_id: str) -> Episode | None:
        """One episode, ``None`` when unknown."""
        return self.episodes.get(episode_id)

    def list_episodes(self, *, scope: Scope | None = None, limit: int = 50) -> Sequence[Episode]:
        """Episodes of one perimeter (or every perimeter), the newest first."""
        rows = filter_scope(tuple(self.episodes.values()), scope)
        return newest_first(rows, key=lambda row: row_key(row.created_at, row.episode_id), limit=limit)


class LessonRows:
    """Lessons: what several close episodes teach (chapter 14 ``Lecon``)."""

    def put_lesson(self, lesson: Lesson) -> None:
        """Store a lesson, replacing the previous row of the same id."""
        self.lessons[lesson.lesson_id] = lesson

    def get_lesson(self, lesson_id: str) -> Lesson | None:
        """One lesson, ``None`` when unknown."""
        return self.lessons.get(lesson_id)

    def list_lessons(self, *, scope: Scope | None = None) -> Sequence[Lesson]:
        """Lessons of one perimeter, the newest first."""
        rows = filter_scope(tuple(self.lessons.values()), scope)
        return newest_first(rows, key=lambda row: row_key(row.created_at, row.lesson_id))


class FicheRows:
    """Fiches, versioned: a correction never overwrites its predecessor (EF-ENT-04)."""

    def put_fiche(self, fiche: Fiche) -> None:
        """Store one version of a fiche under ``(fiche_id, version)``."""
        self.fiches[(fiche.fiche_id, fiche.version)] = fiche

    def get_fiche(self, fiche_id: str, version: int | None = None) -> Fiche | None:
        """One version of a fiche; the latest one when no version is asked."""
        if version is not None:
            return self.fiches.get((fiche_id, version))
        versions = self.versions_of(fiche_id)
        return versions[-1] if versions else None

    def search_fiches(self, *, scope: Scope, terms: Sequence[str], limit: int = 10) -> Sequence[Fiche]:
        """Fiches of one perimeter matching ``terms``, best first.

        Scoped, never global: the caller merges its own perimeter and the common
        one itself, in that order (EF-RES-04). Nothing comes back when no term
        survives normalisation - the whole catalogue is never an answer.
        """
        words = query_words(terms)
        if not words:
            return ()
        return rank(self.list_fiches(scope=scope), words, limit=limit)

    def list_fiches(self, *, scope: Scope | None = None, level: Level | None = None) -> Sequence[Fiche]:
        """The current version of every fiche, filtered by perimeter and level."""
        rows = [
            fiche
            for fiche in self.latest_fiches()
            if (scope is None or fiche.scope == scope) and (level is None or fiche.level is level)
        ]
        return tuple(rows)


class UsageRows:
    """Usages and their feedback: the exact counters of the trust (EF-RET-01/05)."""

    def put_usage(self, usage: Usage) -> None:
        """Store one consultation."""
        self.usages[usage.usage_id] = usage

    def get_usage(self, usage_id: str) -> Usage | None:
        """One consultation, ``None`` when unknown."""
        return self.usages.get(usage_id)

    def put_feedback(self, feedback: Feedback) -> None:
        """Store one answer to a served object; a usage gets at most one answer."""
        self.feedbacks = [kept for kept in self.feedbacks if kept.usage_id != feedback.usage_id]
        self.feedbacks.append(feedback)

    def list_feedback(self, fiche_id: str) -> Sequence[Feedback]:
        """Every feedback recorded about one fiche, oldest first."""
        rows = (feedback for feedback in self.feedbacks if feedback.fiche_id == fiche_id)
        return oldest_first(rows, key=lambda row: row_key(row.at, row.usage_id))
