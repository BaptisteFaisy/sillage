"""Catalogue of Sillage: trust levels, computed notes, fiche versions.

Public surface of the module. Everything is offline and computed by code: no
model call, no network, no AWS import at this level (``catalog.dynamo`` is the
one that imports boto3, inside its constructor).
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
    "NEUTRAL_RATE",
    "SET_ASIDE_REASON",
    "TRANSITIONS",
    "apply_feedback",
    "bump",
    "can_transition",
    "catalogue_metrics",
    "estimate_tokens",
    "freshness_days",
    "history",
    "is_stale",
    "next_level_after_successes",
    "served",
    "should_set_aside",
    "success_rate",
]
