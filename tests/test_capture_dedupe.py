"""Deduplication: one deterministic key per finished job (EF-CAP-11, R3).

The fingerprint is what makes a capture idempotent: the same job received twice
- a retried EventBridge event, an operator who clicks twice - must produce one
episode. These tests pin down what belongs to the key (the work) and what does
not (the hour of reception, the bookkeeping added afterwards).
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from sillage.capture import sample
from sillage.capture.dedupe import (
    VOLATILE_ORIGIN_KEYS,
    canonical_json,
    canonical_trace,
    fingerprint,
    is_duplicate,
    raw_key,
    remember,
)
from sillage.catalog.local import LocalCatalog
from sillage.kernel.ids import pseudonym, trace_fingerprint
from sillage.kernel.types import Trace, TraceEvent


@pytest.fixture
def catalog(clock) -> LocalCatalog:
    return LocalCatalog(clock)


def with_event(trace: Trace, index: int, event: TraceEvent) -> Trace:
    """The same trace with one element replaced."""
    events = list(trace.events)
    events[index] = event
    return replace(trace, events=tuple(events))


class TestFingerprint:
    """The key is a function of the work, and of nothing else."""

    def test_the_same_trace_gives_the_same_key(self) -> None:
        assert fingerprint(sample.ticket_2041()) == fingerprint(sample.ticket_2041())

    def test_the_key_is_built_by_the_kernel(self) -> None:
        trace = sample.ticket_2041()
        data = canonical_trace(trace)
        data.pop("receivedAt", None)
        expected = trace_fingerprint(
            trace.work_item_id,
            json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
        )
        assert fingerprint(trace) == expected
        assert len(fingerprint(trace)) == 16

    def test_a_changed_word_changes_the_key(self) -> None:
        trace = sample.ticket_2041()
        changed = replace(trace.events[1], text=trace.events[1].text + " Et puis non.")
        assert fingerprint(trace) != fingerprint(with_event(trace, 1, changed))

    def test_the_order_of_the_events_matters(self) -> None:
        trace = sample.ticket_2041()
        swapped = (trace.events[1], trace.events[0], *trace.events[2:])
        assert fingerprint(trace) != fingerprint(replace(trace, events=swapped))

    def test_another_job_is_another_key(self) -> None:
        assert fingerprint(sample.ticket_2041()) != fingerprint(sample.investigation_77())

    def test_another_perimeter_is_another_key(self) -> None:
        trace = sample.merged_fix(client="b")
        moved = replace(trace, scope=sample.merged_fix(client="c").scope)
        assert fingerprint(trace) != fingerprint(moved)

    def test_the_reception_instant_changes_nothing(self) -> None:
        trace = sample.ticket_2041()
        later = replace(trace, received_at=trace.closed_at)
        assert fingerprint(trace) == fingerprint(later)

    def test_the_technology_order_changes_nothing(self) -> None:
        trace = sample.ticket_2041()
        shuffled = replace(trace, technologies=tuple(reversed(trace.technologies)))
        assert fingerprint(trace) == fingerprint(shuffled)

    @pytest.mark.parametrize("key", sorted(VOLATILE_ORIGIN_KEYS))
    def test_the_bookkeeping_of_a_capture_changes_nothing(self, key: str) -> None:
        trace = sample.ticket_2041()
        annotated = replace(trace, origin={**trace.origin, key: "valeur"})
        assert fingerprint(trace) == fingerprint(annotated)

    def test_the_work_item_id_stays_in_the_key(self) -> None:
        trace = sample.ticket_2041()
        renamed = replace(trace, work_item_id="guichet:autre")
        assert fingerprint(trace) != fingerprint(renamed)


class TestCanonicalJson:
    """The raw copy: stable bytes, readable French, no surprise."""

    def test_the_dictionary_order_changes_nothing(self) -> None:
        trace = sample.ticket_2041()
        shuffled = replace(trace, origin=dict(reversed(list(trace.origin.items()))))
        assert canonical_json(trace) == canonical_json(shuffled)

    def test_the_events_are_all_there(self) -> None:
        trace = sample.ticket_2041()
        payload = canonical_json(trace)
        for event in trace.events:
            assert event.text in payload

    def test_the_accented_text_stays_readable(self) -> None:
        assert "ingénieur" in canonical_json(sample.ticket_2041())
        assert "\\u" not in canonical_json(sample.ticket_2041())

    def test_the_authors_stay_pseudonyms(self) -> None:
        payload = canonical_json(sample.investigation_77())
        assert pseudonym("alice") in payload
        assert "alice" not in payload


class TestStore:
    """The catalogue index: seen once, recognised the second time."""

    def test_a_new_trace_is_not_a_duplicate(self, catalog) -> None:
        assert is_duplicate(catalog, sample.ticket_2041()) is False

    def test_a_remembered_trace_is_a_duplicate(self, catalog) -> None:
        trace = sample.ticket_2041()
        key = remember(catalog, trace)
        assert key == fingerprint(trace)
        assert is_duplicate(catalog, trace) is True

    def test_the_index_points_at_the_job(self, catalog) -> None:
        trace = sample.ticket_2041()
        remember(catalog, trace)
        assert catalog.trace_seen(fingerprint(trace)) is True
        assert fingerprint(sample.merged_fix()) not in catalog.fingerprints

    def test_another_job_is_still_new(self, catalog) -> None:
        remember(catalog, sample.ticket_2041())
        assert is_duplicate(catalog, sample.investigation_77()) is False


class TestRawKey:
    """EF-CAP-13: one key per job, per perimeter, so a re-capture is a version."""

    def test_the_key_is_built_from_the_perimeter_and_the_job(self) -> None:
        assert raw_key(sample.ticket_2041()) == "traces/client-a/guichet-ticket-2041.json"

    def test_the_same_job_gives_the_same_key(self) -> None:
        assert raw_key(sample.ticket_2041()) == raw_key(sample.ticket_2041())

    def test_two_perimeters_never_share_a_key(self) -> None:
        assert raw_key(sample.merged_fix(client="b")) != raw_key(sample.merged_fix(client="c"))

    def test_the_key_has_no_secret_and_no_space(self) -> None:
        key = raw_key(sample.ticket_2041())
        assert " " not in key and key.endswith(".json")
