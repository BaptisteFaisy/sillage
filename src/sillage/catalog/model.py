"""Row shapes of the catalogue: (de)serialisation for DynamoDB.

Chapter 14 of the specification keys the catalogue on the perimeter and stores
**exact state** there, because a level, a counter or a usage is computed by code
and must never depend on a model (ENF-IA-01). One function pair per entity turns
a kernel dataclass into an item and back:

* here: ``Client``, the ``Travail`` row (the normalised finished job), ``Usage``,
  ``Feedback``, ``JournalEntry``, plus the small fingerprint index that makes
  capture idempotent (EF-CAP-11);
* in :mod:`sillage.catalog.model_knowledge`: ``Episode``, ``Lesson``, ``Fiche``.

The item vocabulary (``S``/``N``/``BOOL``/``L``/``M``/``SS``, ISO 8601 dates,
omitted optional attributes) lives in :mod:`sillage.catalog.attrs`, shared with
:mod:`sillage.catalog.dynamo`, so both stores write and read the very same rows.

Authors are pseudonyms carried in an ``author`` attribute: never in a key, never
in a count, so the catalogue cannot produce a per-person figure (ENF-RH-01).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sillage.catalog.attrs import (
    ENTITY,
    boolean,
    flag,
    instant,
    integer,
    list_of,
    map_of,
    mapping_of_strings,
    member,
    moment,
    moment_or_epoch,
    n,
    put_attr,
    s,
    scope_from_item,
    scope_to_item,
    string_set,
    strings,
    text,
)

# Re-exported so a caller imports every pair from one module.
from sillage.catalog.model_knowledge import (
    EPISODE_ENTITY,
    FICHE_ENTITY,
    LESSON_ENTITY,
    episode_from_item,
    episode_to_item,
    fiche_from_item,
    fiche_to_item,
    lesson_from_item,
    lesson_to_item,
)
from sillage.kernel.types import (
    AgentMode,
    AuthorKind,
    Client,
    Decision,
    Feedback,
    FeedbackResult,
    JournalEntry,
    PrincipalRole,
    Source,
    Trace,
    Usage,
)

#: Capture states of a ``Travail`` row (chapter 14, "état de capture").
CAPTURE_STORED = "stored"  # the raw copy is in the BlobStore, no event written yet
CAPTURE_EVENTS = "events_created"  # the trace was replayed into AgentCore Memory
CAPTURE_SKIPPED = "skipped"  # "do not keep": stored, never distilled (EF-CAP-12)

#: Entity names of the families serialised in this module.
CLIENT_ENTITY = "client"
TRAVAIL_ENTITY = "travail"
USAGE_ENTITY = "usage"
FEEDBACK_ENTITY = "feedback"
JOURNAL_ENTITY = "journal"
FINGERPRINT_ENTITY = "fingerprint"


# -- client ----------------------------------------------------------------- #
def client_to_item(client: Client) -> dict:
    """Row of a tenant: name, "pas de mise en commun" option, retention delays."""
    item = {
        ENTITY: s(CLIENT_ENTITY),
        "clientId": s(client.client_id),
        "name": s(client.name),
        "noCommon": boolean(client.no_common),
    }
    put_attr(item, "technologies", string_set(client.technologies))
    if client.retention_days:
        item["retentionDays"] = {"M": {key: n(value) for key, value in client.retention_days.items()}}
    return item


def client_from_item(item: Mapping[str, Any]) -> Client:
    """Read a tenant row, tolerant to missing fields."""
    delays = (item.get("retentionDays") or {}).get("M", {})
    return Client(
        client_id=text(item, "clientId"),
        name=text(item, "name"),
        no_common=flag(item, "noCommon", False),
        retention_days={name: integer(delays, name) for name in delays},
        technologies=strings(item, "technologies"),
    )


# -- travail ---------------------------------------------------------------- #
def travail_to_item(trace: Trace, *, raw_key: str = "", capture_state: str = "") -> dict:
    """Row of a finished job: the ``Travail`` entity of chapter 14.

    The events are **not** copied here. Chapter 12.4 keeps the normalised trace
    in the object store (``rawKey``) and its events in AgentCore Memory; the
    catalogue holds the exact state: perimeter, source, author kind, pseudonym,
    technologies, dates, capture state and the "do not keep" flag.
    """
    state = capture_state or (CAPTURE_SKIPPED if trace.do_not_distill else CAPTURE_STORED)
    item = {
        ENTITY: s(TRAVAIL_ENTITY),
        "workItemId": s(trace.work_item_id),
        "scope": scope_to_item(trace.scope),
        "source": s(trace.source.value),
        "authorKind": s(trace.author_kind.value),
        "author": s(trace.author),
        "doNotDistill": boolean(trace.do_not_distill),
        "captureState": s(state),
    }
    put_attr(item, "rawKey", s(raw_key) if raw_key else None)
    put_attr(item, "technologies", string_set(trace.technologies))
    put_attr(item, "closedAt", instant(trace.closed_at))
    put_attr(item, "receivedAt", instant(trace.received_at))
    put_attr(item, "origin", map_of(trace.origin))
    return item


def travail_from_item(item: Mapping[str, Any]) -> Trace:
    """Read a ``Travail`` row back into a ``Trace`` (state only, no events)."""
    return Trace(
        work_item_id=text(item, "workItemId"),
        scope=scope_from_item(item),
        source=member(Source, item, "source", Source.SELF_DECLARED),
        author_kind=member(AuthorKind, item, "authorKind", AuthorKind.HUMAN),
        author=text(item, "author"),
        events=(),
        technologies=strings(item, "technologies"),
        closed_at=moment(item, "closedAt"),
        do_not_distill=flag(item, "doNotDistill", False),
        origin=mapping_of_strings(item, "origin"),
        received_at=moment(item, "receivedAt"),
    )


def raw_key_of(item: Mapping[str, Any]) -> str:
    """Object key of the raw trace of a ``Travail`` row, empty when not stored."""
    return text(item, "rawKey")


def capture_state_of(item: Mapping[str, Any]) -> str:
    """Capture state of a ``Travail`` row."""
    return text(item, "captureState", CAPTURE_STORED)


def fingerprint_to_item(fingerprint: str, work_item_id: str) -> dict:
    """Row of the dedupe index: one fingerprint remembers its work item."""
    return {
        ENTITY: s(FINGERPRINT_ENTITY),
        "fingerprint": s(fingerprint),
        "workItemId": s(work_item_id),
    }


def work_item_of_fingerprint(item: Mapping[str, Any]) -> str:
    """Work item a fingerprint row points at, empty when absent."""
    return text(item, "workItemId")


# -- usage and feedback ----------------------------------------------------- #
def usage_to_item(usage: Usage) -> dict:
    """Row of one consultation: what was served, and to which kind of caller."""
    item = {
        ENTITY: s(USAGE_ENTITY),
        "usageId": s(usage.usage_id),
        "servedIds": list_of([s(found) for found in usage.served_ids]),
        "callerRole": s(usage.caller_role.value),
        "callerKind": s(usage.caller_kind.value),
        "problem": s(usage.problem),
    }
    put_attr(item, "callerMode", s(usage.caller_mode.value) if usage.caller_mode else None)
    put_attr(item, "scope", scope_to_item(usage.scope) if usage.scope else None)
    put_attr(item, "at", instant(usage.at))
    return item


def usage_from_item(item: Mapping[str, Any]) -> Usage:
    """Read a usage row, tolerant to missing fields."""
    return Usage(
        usage_id=text(item, "usageId"),
        served_ids=strings(item, "servedIds"),
        caller_role=member(PrincipalRole, item, "callerRole", PrincipalRole.ENGINEER),
        caller_kind=member(AuthorKind, item, "callerKind", AuthorKind.HUMAN),
        caller_mode=member(AgentMode, item, "callerMode", None),
        scope=scope_from_item(item) if "scope" in item else None,
        at=moment(item, "at"),
        problem=text(item, "problem"),
    )


def feedback_to_item(feedback: Feedback) -> dict:
    """Row of one answer to a served knowledge object (chapter 14 ``Retour``)."""
    item = {
        ENTITY: s(FEEDBACK_ENTITY),
        "usageId": s(feedback.usage_id),
        "result": s(feedback.result.value),
        "note": s(feedback.note),
        "ficheId": s(feedback.fiche_id),
        "caller": s(feedback.caller),
    }
    put_attr(item, "at", instant(feedback.at))
    return item


def feedback_from_item(item: Mapping[str, Any]) -> Feedback:
    """Read a feedback row.

    An unreadable result reads as ``DID_NOT_HELP``, never as a success: the
    catalogue never invents a confirmation (ENF-IA-01).
    """
    return Feedback(
        usage_id=text(item, "usageId"),
        result=member(FeedbackResult, item, "result", FeedbackResult.DID_NOT_HELP),
        note=text(item, "note"),
        at=moment(item, "at"),
        fiche_id=text(item, "ficheId"),
        caller=text(item, "caller"),
    )


# -- journal ---------------------------------------------------------------- #
def journal_to_item(entry: JournalEntry) -> dict:
    """Row of one tool call or policy decision (EF-ADM-02, ENF-SEC-05)."""
    item = {
        ENTITY: s(JOURNAL_ENTITY),
        "entryId": s(entry.entry_id),
        "at": s(entry.at.isoformat()),
        "caller": s(entry.caller),
        "callerRole": s(entry.caller_role.value),
        "tool": s(entry.tool),
        "decision": s(entry.decision.value),
    }
    for key, value in (
        ("reason", entry.reason),
        ("scope", entry.scope),
        ("usageId", entry.usage_id),
        ("workItemId", entry.work_item_id),
    ):
        put_attr(item, key, s(value) if value else None)
    return item


def journal_from_item(item: Mapping[str, Any]) -> JournalEntry:
    """Read a journal row; a missing date reads as the epoch, never as "now"."""
    return JournalEntry(
        entry_id=text(item, "entryId"),
        at=moment_or_epoch(item, "at"),
        caller=text(item, "caller"),
        caller_role=member(PrincipalRole, item, "callerRole", PrincipalRole.ENGINEER),
        tool=text(item, "tool"),
        decision=member(Decision, item, "decision", Decision.DEFAULT_DENY),
        reason=text(item, "reason"),
        scope=text(item, "scope"),
        usage_id=text(item, "usageId"),
        work_item_id=text(item, "workItemId"),
    )


__all__ = [
    "CAPTURE_EVENTS",
    "CAPTURE_SKIPPED",
    "CAPTURE_STORED",
    "CLIENT_ENTITY",
    "EPISODE_ENTITY",
    "FEEDBACK_ENTITY",
    "FICHE_ENTITY",
    "FINGERPRINT_ENTITY",
    "JOURNAL_ENTITY",
    "LESSON_ENTITY",
    "TRAVAIL_ENTITY",
    "USAGE_ENTITY",
    "capture_state_of",
    "client_from_item",
    "client_to_item",
    "episode_from_item",
    "episode_to_item",
    "feedback_from_item",
    "feedback_to_item",
    "fiche_from_item",
    "fiche_to_item",
    "fingerprint_to_item",
    "journal_from_item",
    "journal_to_item",
    "lesson_from_item",
    "lesson_to_item",
    "raw_key_of",
    "travail_from_item",
    "travail_to_item",
    "usage_from_item",
    "usage_to_item",
    "work_item_of_fingerprint",
]
