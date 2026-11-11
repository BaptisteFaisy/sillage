"""Normalisation of a finished job into the common trace format (13.1).

AgentCore Memory expects dated events, each with a role among ``USER``,
``ASSISTANT``, ``TOOL`` and ``OTHER``. Sillage puts **the one who resolves** in
the ``ASSISTANT`` role, whether that one is a human or an agent: the episodic
strategy describes what the assistant intended, did and obtained, and the
perimeter is the client, not the person (12.5).

Three rules of 13.1 are enforced here, by code and not by convention:

* every element carries its author between brackets (``[ingénieur]``,
  ``[agent d'exploitation]``, ``[agent de code]``, ``[système]``);
* tool outputs are kept - the AWS documentation says they improve the results;
* the trace ends with an explicit closing event, so the service can tell that
  the episode is over: an episode is only produced once the work is judged
  finished.

The source adapters of :mod:`sillage.capture.sources` stay thin: they map a
payload onto :func:`build_trace`, which is the single place that assembles a
``Trace``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from sillage.capture.authorship import (
    LABEL_ALARM,
    LABEL_COMMAND,
    LABEL_SYSTEM,
    HumanVerdict,
    author_kind_of,
    author_shares,
    event_text,
    label_for,
    verdict_of,
    work_events,
)
from sillage.capture.clock import parse_moment
from sillage.capture.logs import summarise_logs
from sillage.kernel.ids import pseudonym, work_item_id
from sillage.kernel.protocols import ModelBackend
from sillage.kernel.types import (
    AuthorKind,
    Role,
    Scope,
    ScopeKind,
    Source,
    Trace,
    TraceEvent,
)

#: A raw trace: the dictionary a source produces, before normalisation.
RawTrace = Mapping[str, Any]

#: Kind of element -> role accepted by AgentCore Memory (13.1).
ROLE_BY_KIND: dict[str, Role] = {
    "request": Role.USER,
    "action": Role.ASSISTANT,
    "tool_output": Role.TOOL,
    "system": Role.OTHER,
}

#: Role -> kind of element, for a payload that gives a role and no kind.
KIND_BY_ROLE: dict[Role, str] = {value: key for key, value in ROLE_BY_KIND.items()}

#: Closing sentence of a trace that carries none (13.1: an explicit end).
FALLBACK_CLOSING = "[système] Travail terminé."

#: Identity pseudonymised for the events the system writes itself.
SYSTEM_IDENTITY = "systeme"

#: Values of a payload field that say "the monitoring asked, not a human".
ALARM_TRIGGERS = frozenset({"alarme", "alarm", "monitoring"})


def scope_of(raw: Mapping[str, Any]) -> Scope:
    """The perimeter of a raw payload: one client, ``interne`` or ``commun``."""
    block = raw.get("scope")
    if isinstance(block, Mapping) and block:
        kind = ScopeKind(str(block.get("kind", ScopeKind.CLIENT.value)))
        client = block.get("client_id")
        return Scope(kind=kind, client_id=str(client) if client else None)
    client = raw.get("client") or raw.get("clientId") or raw.get("perimetre")
    if client:
        return Scope(kind=ScopeKind.CLIENT, client_id=str(client))
    return Scope(kind=ScopeKind.INTERNAL)


def source_of(raw: Mapping[str, Any]) -> Source:
    """The source of a raw payload; a missing or unknown one reads as Guichet."""
    try:
        return Source(str(raw.get("source", Source.GUICHET.value)))
    except ValueError:
        return Source.GUICHET


def event_of(item: Mapping[str, Any], source: Source, model: ModelBackend | None = None) -> TraceEvent:
    """One normalised event out of one raw element."""
    kind = str(item.get("kind") or "")
    role = _role_of(item.get("role"), kind)
    kind = kind or KIND_BY_ROLE[role]
    author_kind = _author_kind_of(item.get("author_kind"))
    text = str(item.get("text") or "")
    if kind == "tool_output":
        text = summarise_logs(text, model)
    identity = str(item.get("identity") or "")
    return TraceEvent(
        role=role,
        text=event_text(str(item.get("label") or label_for(author_kind, source)), text),
        author=pseudonym(identity) if identity else str(item.get("author") or ""),
        author_kind=author_kind,
        at=parse_moment(item.get("at")),
        kind=kind,
    )


def _role_of(value: Any, kind: str) -> Role:
    """The role of an element: the given one, or the one its kind implies."""
    if value:
        try:
            return Role(str(value).upper())
        except ValueError:
            pass
    return ROLE_BY_KIND.get(kind, Role.OTHER)


def _author_kind_of(value: Any) -> AuthorKind:
    """The kind of an author; anything unreadable reads as a human."""
    try:
        return AuthorKind(str(value or AuthorKind.HUMAN.value).lower())
    except ValueError:
        return AuthorKind.HUMAN


def to_events(ticket: Mapping[str, Any], model: ModelBackend | None = None) -> tuple[TraceEvent, ...]:
    """The events of a closed Guichet ticket (EF-CAP-01, shape of annex B).

    Guichet sends the whole ticket and nothing else is asked of the engineer:
    description, comments, journal extracts and resolution become dated events,
    one role per element, the closing event last.
    """
    resolver = resolver_of(ticket)
    events: list[TraceEvent] = [request_event(ticket)]
    for comment in ticket.get("comments", ()) or ():
        events.extend(comment_events(comment, resolver))
    events.extend(log_events(ticket, model))
    events.append(closing_event(ticket))
    return tuple(events)


def resolver_of(ticket: Mapping[str, Any]) -> str:
    """Who resolved the ticket: the author of the resolution, or nobody."""
    resolution = ticket.get("resolution")
    if isinstance(resolution, Mapping):
        return str(resolution.get("author") or "")
    return str(ticket.get("resolver") or "")


def request_event(ticket: Mapping[str, Any]) -> TraceEvent:
    """The element that opens the work: an alarm, or the description (``USER``)."""
    alarm = str(ticket.get("trigger", "")).lower() in ALARM_TRIGGERS
    label = LABEL_ALARM if alarm else label_for(AuthorKind.HUMAN, Source.GUICHET)
    text = str(ticket.get("description") or ticket.get("title") or "")
    identity = str(ticket.get("requester") or ticket.get("author") or SYSTEM_IDENTITY)
    return TraceEvent(
        role=Role.USER,
        text=event_text(label, text),
        author=pseudonym(identity),
        author_kind=AuthorKind.AGENT if alarm else AuthorKind.HUMAN,
        at=parse_moment(ticket.get("created_at") or ticket.get("closed_at")),
        kind="request",
    )


def comment_events(comment: Mapping[str, Any], resolver: str) -> list[TraceEvent]:
    """One comment, and the tool output that followed it when there is one."""
    identity = str(comment.get("author") or "")
    author_kind = _author_kind_of(comment.get("author_kind"))
    at = parse_moment(comment.get("at"))
    source = Source.KIRO if author_kind is AuthorKind.AGENT else Source.GUICHET
    label = str(comment.get("label") or label_for(author_kind, source))
    actor = pseudonym(identity) if identity else pseudonym(SYSTEM_IDENTITY)
    is_action = comment.get("role") is None and (not resolver or identity == resolver)
    role = _role_of(comment.get("role"), "action" if is_action else "request")
    events = [
        TraceEvent(
            role=role,
            text=event_text(label, str(comment.get("text") or "")),
            author=actor,
            author_kind=author_kind,
            at=at,
            kind=KIND_BY_ROLE[role],
        )
    ]
    output = str(comment.get("tool_output") or "")
    if output:
        events.append(
            TraceEvent(
                role=Role.TOOL,
                text=event_text(LABEL_COMMAND, output),
                author=actor,
                author_kind=author_kind,
                at=at,
                kind="tool_output",
            )
        )
    return events


def log_events(ticket: Mapping[str, Any], model: ModelBackend | None = None) -> list[TraceEvent]:
    """The journal extracts of a ticket, reduced to their useful lines."""
    logs = ticket.get("logs")
    if not logs:
        return []
    entries = logs if isinstance(logs, Sequence) and not isinstance(logs, str) else [logs]
    events = []
    for entry in entries:
        text = summarise_logs(str(entry), model)
        if text:
            events.append(
                TraceEvent(
                    role=Role.TOOL,
                    text=event_text(LABEL_COMMAND, text),
                    author=pseudonym(SYSTEM_IDENTITY),
                    author_kind=AuthorKind.HUMAN,
                    at=parse_moment(ticket.get("closed_at")),
                    kind="tool_output",
                )
            )
    return events


def closing_event(ticket: Mapping[str, Any]) -> TraceEvent:
    """The explicit closing event of a ticket (13.1: the episode is over)."""
    resolution = ticket.get("resolution")
    confirmed = not isinstance(resolution, Mapping) or bool(resolution.get("confirmed", True))
    body = (
        "Ticket clos. Résolution confirmée par le demandeur."
        if confirmed
        else "Ticket clos sans résolution confirmée."
    )
    return TraceEvent(
        role=Role.OTHER,
        text=event_text(LABEL_SYSTEM, body),
        author=pseudonym(SYSTEM_IDENTITY),
        author_kind=AuthorKind.AGENT,
        at=parse_moment(ticket.get("closed_at")),
        kind="system",
    )


def ensure_closing(events: Iterable[TraceEvent]) -> tuple[TraceEvent, ...]:
    """End the trace with a system event, adding one when the source gave none."""
    ordered = list(events)
    if ordered and ordered[-1].role is Role.OTHER and ordered[-1].kind == "system":
        return tuple(ordered)
    last_at = ordered[-1].at if ordered else None
    ordered.append(
        TraceEvent(
            role=Role.OTHER,
            text=FALLBACK_CLOSING,
            author=pseudonym(SYSTEM_IDENTITY),
            author_kind=AuthorKind.AGENT,
            at=last_at,
            kind="system",
        )
    )
    return tuple(ordered)


def build_trace(
    *,
    source: Source,
    scope: Scope,
    events: Sequence[TraceEvent],
    external_id: str = "",
    author: str = "",
    technologies: Iterable[str] = (),
    closed_at: datetime | None = None,
    do_not_distill: bool = False,
    origin: Mapping[str, str] | None = None,
    received_at: datetime | None = None,
    verdict: HumanVerdict | None = None,
) -> Trace:
    """Assemble the normalised trace: the single place a ``Trace`` is built.

    The author kind is **computed** from the events, never declared: a job an
    agent started and a human finished is one episode, ``MIXED``, with the share
    of each (EF-CAP-09). The author of the trace is the one who resolved, under
    a pseudonym (EF-CAP-04, ENF-RH-01).
    """
    ordered = list(events)
    finished = _finished_at(ordered, closed_at)
    if verdict is not None:
        ordered.insert(_verdict_index(ordered), _dated(verdict.event(), finished))
    ordered = list(ensure_closing(ordered))
    work = work_events(tuple(ordered))
    shares = author_shares(tuple(ordered))
    final_origin = _origin_of(origin, shares, verdict)
    return Trace(
        work_item_id=identity_of(source, external_id, origin),
        scope=scope,
        source=source,
        author_kind=author_kind_of(tuple(ordered)),
        author=resolver_of_events(work) or pseudonym(author or SYSTEM_IDENTITY),
        events=tuple(ordered),
        technologies=tuple(dict.fromkeys(str(item) for item in technologies if item)),
        closed_at=finished,
        do_not_distill=do_not_distill,
        origin=final_origin,
        received_at=received_at,
    )


def _dated(event: TraceEvent, moment: datetime | None) -> TraceEvent:
    """Give an instant to an event that has none: the end of the work."""
    return event if event.at is not None or moment is None else replace(event, at=moment)


def resolver_of_events(work: Sequence[TraceEvent]) -> str:
    """The pseudonym of the one who resolved: the last work element naming somebody."""
    for event in reversed(work):
        if event.author:
            return event.author
    return ""


def _verdict_index(events: Sequence[TraceEvent]) -> int:
    """Where the verdict goes: just before the closing event."""
    if events and events[-1].role is Role.OTHER and events[-1].kind == "system":
        return len(events) - 1
    return len(events)


def _finished_at(events: Sequence[TraceEvent], closed_at: datetime | None) -> datetime | None:
    """The instant the work ended: the given one, or the date of the last element."""
    if closed_at is not None:
        return closed_at
    for event in reversed(events):
        if event.at is not None:
            return event.at
    return None


def _origin_of(
    origin: Mapping[str, str] | None, shares: dict[str, int], verdict: HumanVerdict | None
) -> dict[str, str]:
    """Catalogue attributes: the given ones, the share of each, the verdict."""
    final: dict[str, str] = {str(key): str(value) for key, value in (origin or {}).items()}
    final["partAgent"] = str(shares.get(AuthorKind.AGENT.value, 0))
    final["partHumain"] = str(shares.get(AuthorKind.HUMAN.value, 0))
    if verdict is not None:
        final.update({key: value for key, value in verdict.origin().items() if value})
    return final


def identity_of(source: Source, external_id: str, origin: Mapping[str, str] | None) -> str:
    """Identity of the finished job: given by the source, or built from it."""
    given = str((origin or {}).get("workItemId") or "")
    if given:
        return given
    return work_item_id(source.value, external_id or source.value)


def normalize(raw: RawTrace, model: ModelBackend | None = None) -> Trace:
    """Normalise a raw payload of any source into the common ``Trace`` format."""
    source = source_of(raw)
    events = [event_of(item, source, model) for item in raw.get("events", ()) or ()]
    return build_trace(
        source=source,
        scope=scope_of(raw),
        events=events,
        external_id=str(raw.get("external_id") or raw.get("id") or ""),
        author=str(raw.get("author") or ""),
        technologies=raw.get("technologies", ()) or (),
        closed_at=parse_moment(raw.get("closed_at")),
        do_not_distill=bool(raw.get("do_not_distill", False)),
        origin=raw.get("origin") or {},
        received_at=parse_moment(raw.get("received_at")),
        verdict=verdict_of(raw),
    )


__all__ = [
    "FALLBACK_CLOSING",
    "KIND_BY_ROLE",
    "ROLE_BY_KIND",
    "RawTrace",
    "build_trace",
    "closing_event",
    "comment_events",
    "ensure_closing",
    "event_of",
    "identity_of",
    "log_events",
    "normalize",
    "parse_moment",
    "request_event",
    "resolver_of",
    "resolver_of_events",
    "scope_of",
    "source_of",
    "summarise_logs",
    "to_events",
]
