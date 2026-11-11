"""Rows of the catalogue: round trip and DynamoDB shapes for every entity.

Two things are checked here, and only these: that a kernel object written to an
item reads back identical, and that the item really uses the low-level DynamoDB
types (``S``, ``N``, ``BOOL``, ``L``, ``M``, ``SS``) with ISO 8601 dates. A third
group of tests checks the tolerance the readers promise: a missing field, an
unknown enum member or an absent optional value must degrade to the dataclass
default, never raise.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from sillage.catalog import model
from sillage.kernel.types import (
    AgentMode,
    AuthorKind,
    Claim,
    Client,
    Decision,
    Episode,
    Feedback,
    FeedbackResult,
    Fiche,
    JournalEntry,
    Lesson,
    Level,
    Outcome,
    PrincipalRole,
    Scope,
    ScopeKind,
    Source,
    Trace,
    Turn,
    Usage,
)

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
CASE = {
    "work_item_id": "guichet:2041",
    "scope": Scope(kind=ScopeKind.CLIENT, client_id="a"),
    "source": Source.GUICHET,
    "author_kind": AuthorKind.HUMAN,
    "author": "p-7f3a",
    "technologies": ("postgresql", "ecs"),
    "closed_at": NOW,
    "do_not_distill": False,
    "origin": {"ticket": "2041"},
    "received_at": NOW,
}


def a_client() -> Client:
    return Client(
        client_id="a",
        name="Client A - retail",
        no_common=True,
        retention_days={"episode": 730, "fiche": 1825},
        technologies=("postgresql", "ecs"),
    )


def a_trace() -> Trace:
    return Trace(**CASE)


def an_episode() -> Episode:
    return Episode(
        episode_id="ep-1",
        scope=CASE["scope"],
        work_item_id=CASE["work_item_id"],
        situation="500 sur le site apres un deploiement",
        intent="retablir le service",
        turns=(Turn("redemarrer", "base saturee", "aucun effet"),),
        outcome=Outcome.MIXED,
        justification="redemarrage insuffisant",
        teachings=("verifier le pool",),
        failed_paths=("redemarrage",),
        memory_record_id="rec-1",
        source=Source.GUICHET,
        author_kind=AuthorKind.MIXED,
        author="p-7f3a",
        technologies=("postgresql",),
        level=Level.RAW,
        created_at=NOW,
        rejected_diagnosis="base sous-dimensionnee",
        corrected_by="p-1122",
    )


def a_lesson() -> Lesson:
    return Lesson(
        lesson_id="le-1",
        scope=CASE["scope"],
        use_case="connexions epuisees apres deploiement",
        advice=("verifier le pool",),
        pitfalls=("redemarrer sans verifier",),
        confidence=0.8,
        episode_ids=("ep-1", "ep-2"),
        memory_record_id="rec-2",
        level=Level.PROPOSED,
        created_at=NOW,
    )


def a_fiche() -> Fiche:
    return Fiche(
        fiche_id="f-1",
        scope=CASE["scope"],
        version=3,
        title="Connexions a la base epuisees",
        body="Le pool de connexions a ete porte de 10 a 50 par instance.",
        steps=("verifier le pool", "revenir a 10"),
        avoid=("redemarrer sans verifier", "augmenter le pool"),
        validity="postgresql 14, ecs",
        claims=(Claim("le pool est trop grand", ("ep-1",)), Claim("sans source", ())),
        sources=("guichet:2041", "guichet:2042"),
        level=Level.VALIDATED,
        validated_by="p-referent",
        validated_at=NOW,
        memory_record_id="rec-3",
        success_count=4,
        failure_count=1,
        no_feedback_count=2,
        consecutive_failures=0,
        distinct_authors=("p-1", "p-2"),
        last_confirmation=NOW,
        provenance="distillation",
        technologies=("postgresql",),
        set_aside_reason="",
        lesson_id="le-1",
    )


def a_usage() -> Usage:
    return Usage(
        usage_id="u-1",
        served_ids=("f-1", "ep-1"),
        caller_role=PrincipalRole.AGENT,
        caller_kind=AuthorKind.AGENT,
        caller_mode=AgentMode.AUTONOMOUS,
        scope=CASE["scope"],
        at=NOW,
        problem="erreurs 500",
    )


def a_feedback() -> Feedback:
    return Feedback(
        usage_id="u-1",
        result=FeedbackResult.HELPED,
        note="a aide",
        at=NOW,
        fiche_id="f-1",
        caller="p-7f3a",
    )


def a_journal() -> JournalEntry:
    return JournalEntry(
        entry_id="j-1",
        at=NOW,
        caller="p-7f3a",
        caller_role=PrincipalRole.ENGINEER,
        tool="recall",
        decision=Decision.FORBID,
        reason="hors perimetre",
        scope="client-a",
        usage_id="u-1",
        work_item_id="guichet:2041",
    )


PAIRS = [
    ("client", model.client_to_item, model.client_from_item, a_client),
    ("travail", model.travail_to_item, model.travail_from_item, a_trace),
    ("episode", model.episode_to_item, model.episode_from_item, an_episode),
    ("lesson", model.lesson_to_item, model.lesson_from_item, a_lesson),
    ("fiche", model.fiche_to_item, model.fiche_from_item, a_fiche),
    ("usage", model.usage_to_item, model.usage_from_item, a_usage),
    ("feedback", model.feedback_to_item, model.feedback_from_item, a_feedback),
    ("journal", model.journal_to_item, model.journal_from_item, a_journal),
]


#: Fields written as a DynamoDB string set: DynamoDB has no order in a set, so a
#: round trip gives the same members in another order and compares as a set.
UNORDERED = ("technologies", "teachings", "failed_paths", "distinct_authors", "episode_ids")


def normalise(entity):
    """The entity with every set-typed field sorted, for a fair round-trip compare."""
    changes = {
        name: tuple(sorted(getattr(entity, name)))
        for name in UNORDERED
        if isinstance(getattr(entity, name, None), tuple)
    }
    return replace(entity, **changes) if changes else entity


@pytest.mark.parametrize("name,to_item,from_item,build", PAIRS, ids=[pair[0] for pair in PAIRS])
def test_round_trip_is_identity(name: str, to_item, from_item, build) -> None:
    entity = build()
    assert normalise(from_item(to_item(entity))) == normalise(entity), name


@pytest.mark.parametrize("name,to_item,from_item,build", PAIRS, ids=[pair[0] for pair in PAIRS])
def test_every_attribute_is_a_typed_value(name: str, to_item, from_item, build) -> None:
    item = to_item(build())
    for attribute, value in item.items():
        assert isinstance(value, dict) and len(value) == 1, (name, attribute, value)
        assert next(iter(value)) in {"S", "N", "BOOL", "L", "M", "SS", "NULL"}, (name, attribute)


def test_the_travail_row_holds_the_state_and_not_the_events() -> None:
    trace = Trace(**{**CASE, "events": ()})
    item = model.travail_to_item(trace, raw_key="traces/t-1.json", capture_state="events_created")
    assert item["captureState"] == {"S": "events_created"}
    assert item["rawKey"] == {"S": "traces/t-1.json"}
    assert item["doNotDistill"] == {"BOOL": False}
    assert "events" not in item
    assert model.raw_key_of(item) == "traces/t-1.json"
    assert model.capture_state_of(item) == "events_created"


def test_a_do_not_keep_travail_is_marked_skipped() -> None:
    trace = Trace(**{**CASE, "do_not_distill": True})
    item = model.travail_to_item(trace)
    assert item["captureState"] == {"S": model.CAPTURE_SKIPPED}
    assert item["doNotDistill"] == {"BOOL": True}
    assert model.travail_from_item(item).do_not_distill is True


def test_dates_are_stored_as_iso_8601() -> None:
    item = model.fiche_to_item(a_fiche())
    assert item["validatedAt"] == {"S": NOW.isoformat()}
    assert datetime.fromisoformat(item["lastConfirmation"]["S"]) == NOW
    assert isinstance(item["version"], dict) and "N" in item["version"]


def test_numbers_are_stored_as_n() -> None:
    item = model.fiche_to_item(a_fiche())
    assert item["successCount"] == {"N": "4"}
    assert model.lesson_to_item(a_lesson())["confidence"] == {"N": "0.8"}


def test_technologies_are_a_string_set() -> None:
    assert model.client_to_item(a_client())["technologies"] == {"SS": ["ecs", "postgresql"]}


def test_an_empty_set_is_omitted_because_dynamodb_refuses_it() -> None:
    item = model.client_to_item(Client(client_id="b", name="B", technologies=()))
    assert "technologies" not in item


def test_steps_and_sources_are_ordered_lists() -> None:
    item = model.fiche_to_item(a_fiche())
    assert item["steps"] == {"L": [{"S": "verifier le pool"}, {"S": "revenir a 10"}]}
    assert [entry["S"] for entry in item["sources"]["L"]] == ["guichet:2041", "guichet:2042"]


def test_claims_keep_their_episode_references() -> None:
    item = model.fiche_to_item(a_fiche())
    first = item["claims"]["L"][0]["M"]
    assert first["text"] == {"S": "le pool est trop grand"}
    assert first["episodeIds"] == {"L": [{"S": "ep-1"}]}


def test_the_scope_is_a_map_with_its_kind() -> None:
    item = model.fiche_to_item(a_fiche())
    assert item["scope"]["M"]["kind"] == {"S": "client"}
    assert item["scope"]["M"]["clientId"] == {"S": "a"}


def test_the_common_scope_carries_no_client() -> None:
    fiche = Fiche(fiche_id="f-9", scope=Scope(kind=ScopeKind.COMMON), title="T")
    assert "clientId" not in model.fiche_to_item(fiche)["scope"]["M"]


def test_absolute_dates_keep_their_timezone() -> None:
    item = model.usage_to_item(a_usage())
    assert datetime.fromisoformat(item["at"]["S"]).tzinfo is not None


@pytest.mark.parametrize("name,to_item,from_item,build", PAIRS, ids=[pair[0] for pair in PAIRS])
def test_an_empty_item_reads_without_raising(name: str, to_item, from_item, build) -> None:
    empty = from_item({})
    assert empty is not None, name


def test_a_missing_optional_value_is_absent_not_null() -> None:
    fiche = Fiche(fiche_id="f-2", scope=CASE["scope"], title="Sans validateur")
    item = model.fiche_to_item(fiche)
    assert "validatedBy" not in item
    assert "validatedAt" not in item
    assert "memoryRecordId" not in item
    assert model.fiche_from_item(item).validated_by == ""


def test_an_unknown_enum_reads_as_the_safe_default() -> None:
    item = model.fiche_to_item(a_fiche())
    item["level"] = {"S": "inconnu"}
    assert model.fiche_from_item(item).level is Level.PROPOSED


def test_an_unreadable_date_reads_as_absent() -> None:
    item = model.fiche_to_item(a_fiche())
    item["validatedAt"] = {"S": "hier"}
    assert model.fiche_from_item(item).validated_at is None


def test_a_missing_journal_date_reads_as_the_epoch_never_as_now() -> None:
    entry = model.journal_from_item({"entryId": {"S": "j-2"}})
    assert entry.at.year == 1970
    assert entry.decision is Decision.DEFAULT_DENY


def test_an_unreadable_feedback_is_never_a_success() -> None:
    feedback = model.feedback_from_item({"usageId": {"S": "u-1"}})
    assert feedback.result is FeedbackResult.DID_NOT_HELP


def test_lists_and_sets_are_both_accepted_on_read() -> None:
    item = {"steps": {"SS": ["b", "a"]}, "avoid": {"L": [{"S": "x"}]}}
    fiche = model.fiche_from_item(item)
    assert sorted(fiche.steps) == ["a", "b"]
    assert fiche.avoid == ("x",)


def test_a_client_scope_without_an_id_falls_back_to_the_internal_perimeter() -> None:
    broken = {"scope": {"M": {"kind": {"S": "client"}}}}
    assert model.fiche_from_item(broken).scope == Scope(kind=ScopeKind.INTERNAL)


def test_a_fingerprint_row_points_at_its_work_item() -> None:
    item = model.fingerprint_to_item("abc123", "guichet:2041")
    assert item["fingerprint"] == {"S": "abc123"}
    assert model.work_item_of_fingerprint(item) == "guichet:2041"


def test_the_capture_states_are_distinct() -> None:
    assert len({model.CAPTURE_STORED, model.CAPTURE_EVENTS, model.CAPTURE_SKIPPED}) == 3
