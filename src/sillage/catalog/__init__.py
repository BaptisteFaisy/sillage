"""Catalogue of Sillage: trust levels, computed notes, fiche versions, storage.

Public surface of the module. Everything is offline and computed by code: no
model call, no network, no AWS import at this level - ``catalog.dynamo`` is the
one that imports boto3, inside its constructor.

Re-exported here: the trust state machine (``levels``), the scores (``scoring``),
the versioning (``versions``), the row serialisation (``model``) and the in-memory
store the tests, the demo and the offline portal use (``local``).
"""

from __future__ import annotations

from sillage.catalog.levels import (
    SET_ASIDE_REASON,
    TRANSITIONS,
    apply_feedback,
    can_transition,
    next_level_after_successes,
    should_set_aside,
)
from sillage.catalog.local import ListJournal, ListNotifier, LocalBlobStore, LocalCatalog, Notification
from sillage.catalog.model import (
    CAPTURE_EVENTS,
    CAPTURE_SKIPPED,
    CAPTURE_STORED,
    capture_state_of,
    client_from_item,
    client_to_item,
    episode_from_item,
    episode_to_item,
    feedback_from_item,
    feedback_to_item,
    fiche_from_item,
    fiche_to_item,
    journal_from_item,
    journal_to_item,
    lesson_from_item,
    lesson_to_item,
    raw_key_of,
    travail_from_item,
    travail_to_item,
    usage_from_item,
    usage_to_item,
)
from sillage.catalog.scoring import (
    NEUTRAL_RATE,
    catalogue_metrics,
    estimate_tokens,
    freshness_days,
    is_stale,
    served,
    success_rate,
)
from sillage.catalog.versions import bump, history

__all__ = [
    "CAPTURE_EVENTS",
    "CAPTURE_SKIPPED",
    "CAPTURE_STORED",
    "NEUTRAL_RATE",
    "SET_ASIDE_REASON",
    "TRANSITIONS",
    "ListJournal",
    "ListNotifier",
    "LocalBlobStore",
    "LocalCatalog",
    "Notification",
    "apply_feedback",
    "bump",
    "can_transition",
    "capture_state_of",
    "catalogue_metrics",
    "client_from_item",
    "client_to_item",
    "episode_from_item",
    "episode_to_item",
    "estimate_tokens",
    "feedback_from_item",
    "feedback_to_item",
    "fiche_from_item",
    "fiche_to_item",
    "freshness_days",
    "history",
    "is_stale",
    "journal_from_item",
    "journal_to_item",
    "lesson_from_item",
    "lesson_to_item",
    "next_level_after_successes",
    "raw_key_of",
    "served",
    "should_set_aside",
    "success_rate",
    "travail_from_item",
    "travail_to_item",
    "usage_from_item",
    "usage_to_item",
]
