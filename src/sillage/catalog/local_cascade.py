"""Cascade deletion on the in-memory catalogue (EF-ENT-03, specification 17.2).

The rules and the shape of the report live in :mod:`sillage.catalog.cascade`,
shared with the DynamoDB store; this module only applies a plan to the
dictionaries of :class:`sillage.catalog.local_state.CatalogState`.
"""

from __future__ import annotations

from sillage.catalog.cascade import plan
from sillage.kernel.types import Fiche, Scope


class CascadeRows:
    """Deletion of a perimeter, with the dependent fiches reported (EF-ENT-03)."""

    def delete_scope(self, scope: Scope) -> dict[str, int]:
        """Delete every row of one perimeter and report what disappeared.

        Works first, then the episodes and lessons built on them, then the fiches
        of the perimeter, then the consultations and their answers. Fiches of
        **other** perimeters citing a deleted work item stay, are counted in
        ``fiches_signalees`` and are readable through ``flagged_fiches``.
        """
        cascading = plan(
            scope=scope,
            traces=tuple(self.traces.values()),
            episodes=tuple(self.episodes.values()),
            lessons=tuple(self.lessons.values()),
            fiches=tuple(self.fiches.values()),
            usages=tuple(self.usages.values()),
            feedbacks=tuple(self.feedbacks),
        )
        report = cascading.report()
        self._forget(cascading)
        return report

    def flagged_fiches(self) -> tuple[Fiche, ...]:
        """Fiches kept at the last cascade whose sources no longer exist."""
        return self.flagged

    def _forget(self, cascading) -> None:
        """Remove the rows of a plan from every dictionary of the state."""
        self.flagged = cascading.flagged
        self.traces = {key: row for key, row in self.traces.items() if key not in cascading.works}
        self.episodes = {key: row for key, row in self.episodes.items() if key not in cascading.episodes}
        self.lessons = {key: row for key, row in self.lessons.items() if key not in cascading.lessons}
        self.fiches = {key: row for key, row in self.fiches.items() if key[0] not in cascading.fiches}
        self.usages = {key: row for key, row in self.usages.items() if key not in cascading.usages}
        self.feedbacks = [
            row
            for row in self.feedbacks
            if row.usage_id not in cascading.usages and row.fiche_id not in cascading.fiches
        ]
        # the dedupe index forgets a deleted work item too, or a re-capture would be
        # refused as a duplicate of something that no longer exists (EF-CAP-11)
        self.fingerprints = {
            fingerprint: work
            for fingerprint, work in self.fingerprints.items()
            if work not in cascading.works
        }
