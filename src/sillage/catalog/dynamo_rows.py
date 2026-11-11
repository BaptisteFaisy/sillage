"""Families of the catalogue on DynamoDB: episodes, lessons, fiches, usages.

One mixin, like the offline store, so each family can be read against the table of
chapter 14 on its own. It relies on the plumbing of ``DynamoCatalog`` (``_put``,
``_get``, ``_query``, ``_scan``, ``_delete``, ``row_key``), on the key shapes of
:mod:`sillage.catalog.dynamo_tables` and on the shared cascade rules of
:mod:`sillage.catalog.cascade`.

Three behaviours are worth stating out loud:

* a fiche is read by primary key when a version is asked, and through ``by_id``
  otherwise, where versions are zero padded so the latest comes first (EF-ENT-04);
* ``list_fiches`` keeps the latest version of each fiche and filters the level in
  code: a level is computed, so it is filtered where it is read, not indexed
  (ENF-IA-01);
* ``delete_scope`` reads each family once, applies the shared plan, deletes by
  primary key and reports the counts, plus the fiches kept with a dead source
  (EF-ENT-03, specification 17.2).
"""

from __future__ import annotations

from collections.abc import Sequence

from sillage.catalog.cascade import plan
from sillage.catalog.dynamo_tables import (
    INDEX_BY_ID,
    INDEX_BY_SCOPE,
    episode_keys,
    feedback_keys,
    fiche_keys,
    fiche_order,
    lesson_keys,
    row_index,
    sort_stamp,
    usage_keys,
    version_key,
)
from sillage.catalog.local_search import query_words, rank
from sillage.catalog.local_state import latest_per_fiche
from sillage.catalog.model import (
    episode_from_item,
    episode_to_item,
    feedback_from_item,
    feedback_to_item,
    fiche_from_item,
    fiche_to_item,
    lesson_from_item,
    lesson_to_item,
    travail_from_item,
    usage_from_item,
    usage_to_item,
)
from sillage.kernel.types import Episode, Feedback, Fiche, Lesson, Level, Scope, Usage

#: Families a cascade reads, in the order the plan needs them.
FAMILIES = ("travail", "episode", "lesson", "fiche", "usage", "feedback")


class DynamoRows:
    """``CatalogStore`` families of chapter 14 on the single table."""

    # -- episodes ---------------------------------------------------------- #
    def put_episode(self, episode: Episode) -> None:
        """Write an episode row."""
        order = sort_stamp(episode.created_at, episode.episode_id)
        index = row_index("episode", ident=episode.episode_id, label=episode.scope.actor_id, order=order)
        self._put(episode_keys(episode), episode_to_item(episode), index)

    def get_episode(self, episode_id: str) -> Episode | None:
        """One episode, read from its identifier alone."""
        items = self._by_id("episode", episode_id, limit=1)
        return episode_from_item(items[0]) if items else None

    def list_episodes(self, *, scope: Scope | None = None, limit: int = 50) -> Sequence[Episode]:
        """Episodes of one perimeter, or of every perimeter, the newest first."""
        if scope is not None:
            items = self._scoped("episode", scope, limit=limit)
        else:
            items = self._sorted(self._scan("episode"), newest=True)
        rows = [episode_from_item(item) for item in items]
        return tuple(rows[:limit]) if limit > 0 else tuple(rows)

    # -- lessons ----------------------------------------------------------- #
    def put_lesson(self, lesson: Lesson) -> None:
        """Write a lesson row."""
        order = sort_stamp(lesson.created_at, lesson.lesson_id)
        index = row_index("lesson", ident=lesson.lesson_id, label=lesson.scope.actor_id, order=order)
        self._put(lesson_keys(lesson), lesson_to_item(lesson), index)

    def get_lesson(self, lesson_id: str) -> Lesson | None:
        """One lesson, read from its identifier alone."""
        items = self._by_id("lesson", lesson_id, limit=1)
        return lesson_from_item(items[0]) if items else None

    def list_lessons(self, *, scope: Scope | None = None) -> Sequence[Lesson]:
        """Lessons of one perimeter, or of every perimeter, the newest first."""
        items = self._scoped("lesson", scope) if scope else self._sorted(self._scan("lesson"), newest=True)
        return tuple(lesson_from_item(item) for item in items)

    # -- fiches ------------------------------------------------------------ #
    def put_fiche(self, fiche: Fiche) -> None:
        """Write one version of a fiche; the previous versions stay readable."""
        order = sort_stamp(fiche.validated_at, fiche.fiche_id)
        index = row_index(
            "fiche",
            ident=fiche.fiche_id,
            label=fiche.scope.actor_id,
            order=order,
            id_order=fiche_order(fiche),
        )
        self._put(fiche_keys(fiche), fiche_to_item(fiche), index)

    def get_fiche(self, fiche_id: str, version: int | None = None) -> Fiche | None:
        """One version of a fiche; the latest one when no version is asked."""
        if version is not None:
            found = self._get({"PK": f"FICHE#{fiche_id}", "SK": version_key(version)})
            return fiche_from_item(found) if found else None
        items = self._by_id("fiche", fiche_id, limit=1)
        return fiche_from_item(items[0]) if items else None

    def list_fiches(self, *, scope: Scope | None = None, level: Level | None = None) -> Sequence[Fiche]:
        """The current version of every fiche, filtered by perimeter and level."""
        items = self._scoped("fiche", scope) if scope else self._scan("fiche")
        latest = latest_per_fiche([fiche_from_item(item) for item in items])
        kept = [fiche for fiche in latest if level is None or fiche.level is level]
        return tuple(sorted(kept, key=lambda row: (row.scope.actor_id, row.fiche_id)))

    def search_fiches(self, *, scope: Scope, terms: Sequence[str], limit: int = 10) -> Sequence[Fiche]:
        """Fiches of one perimeter matching ``terms``, best first.

        The ranking is the offline one (``local_search``): one implementation of
        the lexical search for both stores, so a test cannot pass on one and fail
        on the other.
        """
        words = query_words(terms)
        if not words:
            return ()
        return rank(self.list_fiches(scope=scope), words, limit=limit)

    # -- usages and feedback ----------------------------------------------- #
    def put_usage(self, usage: Usage) -> None:
        """Write a consultation."""
        order = sort_stamp(usage.at, usage.usage_id)
        index = row_index("usage", ident=usage.usage_id, label=usage.scope or "", order=order)
        self._put(usage_keys(usage), usage_to_item(usage), index)

    def get_usage(self, usage_id: str) -> Usage | None:
        """One consultation, ``None`` when unknown."""
        found = self._get({"PK": f"USAGE#{usage_id}", "SK": "USAGE"})
        return usage_from_item(found) if found else None

    def put_feedback(self, feedback: Feedback) -> None:
        """Write the answer to a consultation; a usage has at most one answer."""
        order = sort_stamp(feedback.at, feedback.usage_id)
        index = row_index("feedback", ident=feedback.usage_id, label=feedback.fiche_id, order=order)
        self._put(feedback_keys(feedback), feedback_to_item(feedback), index)

    def list_feedback(self, fiche_id: str) -> Sequence[Feedback]:
        """Every answer recorded about one fiche, oldest first."""
        items = self._query(
            pk_name="gsi1pk",
            pk=f"feedback#{fiche_id}",
            sk_name="gsi1sk",
            index=INDEX_BY_SCOPE,
            descending=False,
        )
        return tuple(feedback_from_item(item) for item in items)

    # -- cascade ----------------------------------------------------------- #
    def delete_scope(self, scope: Scope) -> dict[str, int]:
        """Delete one perimeter and report what left, family by family (17.2)."""
        items = {family: self._scan(family) for family in FAMILIES}
        cascading = plan(
            scope=scope,
            traces=tuple(travail_from_item(row) for row in items["travail"]),
            episodes=tuple(episode_from_item(row) for row in items["episode"]),
            lessons=tuple(lesson_from_item(row) for row in items["lesson"]),
            fiches=tuple(fiche_from_item(row) for row in items["fiche"]),
            usages=tuple(usage_from_item(row) for row in items["usage"]),
            feedbacks=tuple(feedback_from_item(row) for row in items["feedback"]),
        )
        self.flagged = cascading.flagged
        for row in items["travail"]:
            if travail_from_item(row).work_item_id in cascading.works:
                self._delete(self.row_key(row))
        for row in items["episode"]:
            if episode_from_item(row).episode_id in cascading.episodes:
                self._delete(self.row_key(row))
        for row in items["lesson"]:
            if lesson_from_item(row).lesson_id in cascading.lessons:
                self._delete(self.row_key(row))
        for row in items["fiche"]:
            if fiche_from_item(row).fiche_id in cascading.fiches:
                self._delete(self.row_key(row))
        for row in items["usage"]:
            if usage_from_item(row).usage_id in cascading.usages:
                self._delete(self.row_key(row))
        for row in items["feedback"]:
            if feedback_from_item(row).usage_id in cascading.usages:
                self._delete(self.row_key(row))
        return cascading.report()

    def flagged_fiches(self) -> tuple[Fiche, ...]:
        """Fiches kept at the last cascade whose sources no longer exist."""
        return self.flagged

    # -- helpers ----------------------------------------------------------- #
    def _by_id(self, entity: str, ident: str, limit: int = 1) -> tuple[dict, ...]:
        """Rows of one identifier, through the ``by_id`` index, newest first."""
        return self._query(
            pk_name="gsi2pk",
            pk=f"{entity}#{ident}",
            sk_name="gsi2sk",
            index=INDEX_BY_ID,
            limit=limit,
        )

    def _scoped(self, entity: str, scope: Scope, limit: int = 0) -> tuple[dict, ...]:
        """Rows of one family in one perimeter, through the ``by_scope`` index."""
        return self._query(
            pk_name="gsi1pk",
            pk=f"{entity}#{scope.actor_id}",
            sk_name="gsi1sk",
            index=INDEX_BY_SCOPE,
            limit=limit,
        )

    @staticmethod
    def _sorted(items: tuple[dict, ...], *, newest: bool) -> tuple[dict, ...]:
        """Items of a scan put in a stable order: ``by_scope`` sort value, then key.

        A scan has no order of its own, and a listing that changes order between
        two calls would break the demo, the portal and the tests.
        """

        def ordering(item: dict) -> tuple[str, str]:
            return (item.get("gsi1sk", {}).get("S", ""), item["SK"]["S"])

        return tuple(sorted(items, key=ordering, reverse=newest))


__all__ = ["FAMILIES", "DynamoRows"]
