"""Levels: the trust state machine of specification chapter 10.2.

Every allowed transition of the diagram is checked, and so is every refused
one (all the other pairs of levels), because a state machine that only says
"yes" would not protect anything.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from sillage.catalog.levels import (
    SET_ASIDE_REASON,
    TRANSITIONS,
    apply_feedback,
    can_transition,
    next_level_after_successes,
    should_set_aside,
)
from sillage.kernel.config import Settings
from sillage.kernel.types import FeedbackResult, Fiche, Level, Scope, ScopeKind

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)

ALLOWED = [(current, target) for current, targets in TRANSITIONS.items() for target in targets]
REFUSED = [(current, target) for current in Level for target in Level if target not in TRANSITIONS[current]]


def make_fiche(**changes: object) -> Fiche:
    """A validated fiche of client a: the starting point of most cases."""
    base = Fiche(
        fiche_id="f-connexions",
        scope=Scope(kind=ScopeKind.CLIENT, client_id="a"),
        title="Connexions a la base epuisees",
        body="Le deploiement de 13h50 a porte le pool de 10 a 50 connexions par instance.",
        level=Level.VALIDATED,
        success_count=2,
        last_confirmation=NOW,
    )
    return replace(base, **changes)


class TestTransitions:
    @pytest.mark.parametrize(("current", "target"), ALLOWED)
    def test_allowed_transition(self, current: Level, target: Level) -> None:
        assert can_transition(current, target) is True

    @pytest.mark.parametrize(("current", "target"), REFUSED)
    def test_refused_transition(self, current: Level, target: Level) -> None:
        assert can_transition(current, target) is False

    def test_a_level_never_transitions_to_itself(self) -> None:
        assert all(not can_transition(level, level) for level in Level)

    def test_withdrawn_is_terminal(self) -> None:
        assert TRANSITIONS[Level.WITHDRAWN] == ()

    def test_set_aside_goes_back_to_validated_by_a_referent(self) -> None:
        assert can_transition(Level.SET_ASIDE, Level.VALIDATED) is True

    def test_codes_are_the_ones_of_the_portal(self) -> None:
        assert len(TRANSITIONS) == len(Level)


class TestProven:
    def test_three_successes_by_two_authors_are_proven(self) -> None:
        level = next_level_after_successes(level=Level.VALIDATED, successes=3, authors=2)
        assert level is Level.PROVEN

    def test_string_levels_are_accepted(self) -> None:
        assert next_level_after_successes(level="validated", successes=3, authors=2) == "proven"

    def test_three_successes_by_one_author_stay_validated(self) -> None:
        level = next_level_after_successes(level=Level.VALIDATED, successes=3, authors=1)
        assert level is Level.VALIDATED

    def test_two_successes_by_two_authors_stay_validated(self) -> None:
        level = next_level_after_successes(level=Level.VALIDATED, successes=2, authors=4)
        assert level is Level.VALIDATED

    def test_an_author_list_is_counted_by_distinct_names(self) -> None:
        level = next_level_after_successes(
            level=Level.VALIDATED, successes=3, authors=("p-7f3a", "p-7f3a", "p-2b19")
        )
        assert level is Level.PROVEN

    def test_a_proposed_fiche_is_not_promoted_by_usage(self) -> None:
        """Validation stays the decision of a referent, never of the counters."""
        level = next_level_after_successes(level=Level.PROPOSED, successes=9, authors=5)
        assert level is Level.PROPOSED

    def test_a_set_aside_fiche_is_not_resurrected_by_usage(self) -> None:
        level = next_level_after_successes(level=Level.SET_ASIDE, successes=9, authors=5)
        assert level is Level.SET_ASIDE

    def test_thresholds_are_settings(self) -> None:
        strict = Settings(proven_successes=5, proven_distinct_authors=3)
        level = next_level_after_successes(level=Level.VALIDATED, successes=4, authors=2, settings=strict)
        assert level is Level.VALIDATED


class TestSetAside:
    def test_one_failure_is_not_enough(self) -> None:
        assert should_set_aside(consecutive_failures=1) is False

    def test_two_consecutive_failures_set_aside(self) -> None:
        assert should_set_aside(consecutive_failures=2) is True

    def test_more_failures_still_set_aside(self) -> None:
        assert should_set_aside(consecutive_failures=5) is True

    def test_the_threshold_is_a_setting(self) -> None:
        patient = Settings(set_aside_consecutive_failures=3)
        assert should_set_aside(consecutive_failures=2, settings=patient) is False
        assert should_set_aside(consecutive_failures=3, settings=patient) is True


class TestApplyFeedback:
    def test_a_positive_feedback_confirms_and_clears_the_failures(self) -> None:
        fiche = make_fiche(success_count=1, failure_count=1, consecutive_failures=1)
        updated = apply_feedback(fiche, FeedbackResult.HELPED, now=NOW, author="p-7f3a")
        assert updated.success_count == 2
        assert updated.failure_count == 1
        assert updated.consecutive_failures == 0
        assert updated.level is Level.VALIDATED

    def test_a_negative_feedback_counts_a_failure(self) -> None:
        updated = apply_feedback(make_fiche(consecutive_failures=0), FeedbackResult.DID_NOT_HELP)
        assert updated.failure_count == 1
        assert updated.consecutive_failures == 1
        assert updated.level is Level.VALIDATED

    def test_wrong_is_a_negative_feedback_too(self) -> None:
        updated = apply_feedback(make_fiche(consecutive_failures=0), FeedbackResult.WRONG)
        assert updated.consecutive_failures == 1

    def test_two_negative_feedbacks_set_the_fiche_aside(self) -> None:
        fiche = apply_feedback(make_fiche(), FeedbackResult.DID_NOT_HELP)
        fiche = apply_feedback(fiche, FeedbackResult.WRONG)
        assert fiche.level is Level.SET_ASIDE
        assert fiche.set_aside_reason == SET_ASIDE_REASON

    def test_a_success_between_two_failures_saves_the_fiche(self) -> None:
        fiche = apply_feedback(make_fiche(), FeedbackResult.DID_NOT_HELP)
        fiche = apply_feedback(fiche, FeedbackResult.HELPED)
        fiche = apply_feedback(fiche, FeedbackResult.DID_NOT_HELP)
        assert fiche.consecutive_failures == 1
        assert fiche.level is Level.VALIDATED

    def test_two_distinct_authors_promote_to_proven(self) -> None:
        fiche = make_fiche(success_count=1, distinct_authors=("p-7f3a",))
        fiche = apply_feedback(fiche, FeedbackResult.HELPED, author="p-7f3a")
        assert fiche.level is Level.VALIDATED
        fiche = apply_feedback(fiche, FeedbackResult.HELPED, author="p-2b19")
        assert fiche.level is Level.PROVEN
        assert fiche.success_count == 3
        assert fiche.distinct_authors == ("p-7f3a", "p-2b19")

    def test_three_successes_by_one_author_stay_validated(self) -> None:
        fiche = make_fiche(success_count=2, distinct_authors=("p-7f3a",))
        fiche = apply_feedback(fiche, FeedbackResult.HELPED, author="p-7f3a")
        assert fiche.success_count == 3
        assert fiche.level is Level.VALIDATED

    def test_a_usage_without_feedback_is_counted_apart(self) -> None:
        """EF-RET-05: it moves its own counter and never the rate."""
        fiche = make_fiche(success_count=1, no_feedback_count=3)
        updated = apply_feedback(fiche, FeedbackResult.HELPED)
        assert updated.no_feedback_count == 2
        assert updated.success_count == 2
        assert updated.failure_count == 0

    def test_the_counter_never_goes_below_zero(self) -> None:
        updated = apply_feedback(make_fiche(no_feedback_count=0), FeedbackResult.HELPED)
        assert updated.no_feedback_count == 0

    def test_the_confirmation_date_follows_the_success(self) -> None:
        later = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
        updated = apply_feedback(make_fiche(), FeedbackResult.HELPED, now=later)
        assert updated.last_confirmation == later

    def test_a_failure_does_not_confirm_anything(self) -> None:
        updated = apply_feedback(make_fiche(), FeedbackResult.DID_NOT_HELP, now=NOW)
        assert updated.last_confirmation == NOW

    def test_without_a_date_the_previous_confirmation_is_kept(self) -> None:
        older = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
        updated = apply_feedback(make_fiche(last_confirmation=older), FeedbackResult.HELPED)
        assert updated.last_confirmation == older

    def test_the_input_fiche_is_never_modified(self) -> None:
        fiche = make_fiche(success_count=2, consecutive_failures=1)
        apply_feedback(fiche, FeedbackResult.HELPED, now=NOW, author="p-7f3a")
        assert fiche.success_count == 2
        assert fiche.consecutive_failures == 1
        assert fiche.distinct_authors == ()

    def test_a_withdrawn_fiche_stays_withdrawn(self) -> None:
        fiche = make_fiche(level=Level.WITHDRAWN, consecutive_failures=1)
        updated = apply_feedback(fiche, FeedbackResult.WRONG)
        assert updated.level is Level.WITHDRAWN
        assert updated.failure_count == 1

    def test_a_set_aside_fiche_keeps_counting_but_stays_aside(self) -> None:
        fiche = make_fiche(level=Level.SET_ASIDE, consecutive_failures=2)
        updated = apply_feedback(fiche, FeedbackResult.HELPED)
        assert updated.level is Level.SET_ASIDE
        assert updated.success_count == 3

    def test_a_proposed_fiche_can_be_set_aside(self) -> None:
        fiche = make_fiche(level=Level.PROPOSED, consecutive_failures=1)
        updated = apply_feedback(fiche, FeedbackResult.WRONG)
        assert updated.level is Level.SET_ASIDE
