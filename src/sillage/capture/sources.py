"""Source adapters: one thin function per way a finished job reaches Sillage.

Chapter 15.2 lists the entry points and 13.2 the three ways to capture the work
of an agent. Each adapter here does one thing only: read the shape its source
produces - a Guichet ticket, an EventBridge event of AWS DevOps Agent, a Kiro
``Stop`` trigger, a merged pull request, an episode an agent declares itself -
and hand it to :func:`sillage.capture.normalize.build_trace`.

Nothing heavy lives here: roles, labels, closing event, log summary, author
kind and shares are computed once, in ``normalize`` and ``authorship``. An
adapter that grew its own rules would be a second implementation of 13.1.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from sillage.capture.authorship import (
    LABEL_ALARM,
    LABEL_COMMAND,
    LABEL_HUMAN,
    LABEL_SYSTEM,
    verdict_of,
)
from sillage.capture.normalize import (
    ALARM_TRIGGERS,
    SYSTEM_IDENTITY,
    build_trace,
    event_of,
    parse_moment,
    scope_of,
    summarise_logs,
    to_events,
)
from sillage.kernel.errors import ValidationError
from sillage.kernel.ids import pseudonym
from sillage.kernel.protocols import ModelBackend
from sillage.kernel.types import AuthorKind, Role, Source, Trace, TraceEvent

#: A source adapter: a payload, an optional model for the journals, a trace.
Adapter = Callable[[Mapping[str, Any], ModelBackend | None], Trace]


def guichet_ticket(payload: Mapping[str, Any], model: ModelBackend | None = None) -> Trace:
    """A ticket Guichet sends when it is closed (EF-CAP-01, journey 8.1)."""
    resolution = payload.get("resolution") if isinstance(payload.get("resolution"), Mapping) else {}
    author = str((resolution or {}).get("author") or payload.get("resolver") or payload.get("author") or "")
    return build_trace(
        source=Source.GUICHET,
        scope=scope_of(payload),
        events=to_events(payload, model),
        external_id=str(payload.get("id") or payload.get("ticket") or ""),
        author=author,
        technologies=payload.get("technologies") or payload.get("technos") or (),
        closed_at=parse_moment(payload.get("closed_at") or payload.get("closedAt")),
        do_not_distill=bool(payload.get("do_not_distill", False)),
        origin=_guichet_origin(payload),
        verdict=verdict_of(payload),
    )


def devops_agent_event(payload: Mapping[str, Any], model: ModelBackend | None = None) -> Trace:
    """An AWS DevOps Agent investigation, as an EventBridge event carries it.

    EF-CAP-05 and 13.2: the product signals the end of the investigation, and
    Sillage goes for the trace. The envelope is accepted with or without its
    ``detail`` wrapper, so a replayed event and a direct call behave the same.
    """
    detail = payload.get("detail") if isinstance(payload.get("detail"), Mapping) else payload
    events = [
        _request(detail.get("alarm") or detail.get("problem") or "", detail),
        *_steps(detail, Source.DEVOPS_AGENT, model),
        _system("Enquête close. Diagnostic transmis.", parse_moment(_ended(detail))),
    ]
    return _trace(Source.DEVOPS_AGENT, detail, events, "investigationId")


def kiro_session(payload: Mapping[str, Any], model: ModelBackend | None = None) -> Trace:
    """A Kiro session, as the ``Stop`` trigger sends it (EF-CAP-07, journey 8.3)."""
    detail = payload.get("detail") if isinstance(payload.get("detail"), Mapping) else payload
    events = [
        _request(detail.get("request") or detail.get("problem") or "", detail),
        *_steps(detail, Source.KIRO, model),
        _system("Session close.", parse_moment(_ended(detail))),
    ]
    return _trace(Source.KIRO, detail, events, "sessionId")


def merged_pull_request(payload: Mapping[str, Any], model: ModelBackend | None = None) -> Trace:
    """A correction merged in the code repository (EF-CAP-08)."""
    detail = payload.get("detail") if isinstance(payload.get("detail"), Mapping) else payload
    events = [
        _request(detail.get("description") or detail.get("title") or "", detail),
        *_steps(detail, Source.MERGE, model),
        *_reviews(detail),
        _system("Correction fusionnée.", parse_moment(_ended(detail, "mergedAt"))),
    ]
    return _trace(Source.MERGE, detail, events, "pullRequest")


#: Keys of the structure ``record_episode`` imposes on a declaring agent.
DECLARED_REQUIRED = ("situation", "intent")

#: Declared outcome -> the French word the closing event carries.
DECLARED_OUTCOME = {"success": "succès", "failure": "échec", "mixed": "mitigée"}


def declared_episode(payload: Mapping[str, Any], model: ModelBackend | None = None) -> Trace:
    """An episode an agent declares itself through ``record_episode`` (EF-CAP-06).

    13.2: this way works with any MCP client, but the agent summarises its own
    work. The trace says so - the closing event and the ``declarePar`` attribute
    keep a reader from taking a summary for a transcript. The imposed structure
    is checked by code, before anything is stored.
    """
    detail = payload.get("detail") if isinstance(payload.get("detail"), Mapping) else payload
    missing = [key for key in DECLARED_REQUIRED if not str(detail.get(key) or "")]
    if missing:
        raise ValidationError(f"structure imposée incomplète : {', '.join(missing)}", missing=tuple(missing))
    outcome = str(detail.get("outcome") or "").lower()
    issue = DECLARED_OUTCOME.get(outcome, "inconnue")
    events = [
        _request(detail.get("situation"), detail),
        _action(detail.get("intent") or "", detail, AuthorKind.AGENT, Source.SELF_DECLARED),
        *_attempts(detail),
        *_evidence(detail, model),
        _system(
            f"Épisode déclaré par l'agent. Issue déclarée : {issue}.",
            parse_moment(_ended(detail)),
        ),
    ]
    declarer = str(detail.get("author") or detail.get("agent") or SYSTEM_IDENTITY)
    extra = {"declarePar": pseudonym(declarer)}
    declared_id = str(detail.get("workItemId") or "")
    if declared_id:
        # the agent names its own job: that identity is kept as it is
        extra["workItemId"] = declared_id
    return _trace(Source.SELF_DECLARED, detail, events, "workItemId", extra_origin=extra)


#: Which adapter serves which source.
ADAPTERS: dict[Source, Adapter] = {
    Source.GUICHET: guichet_ticket,
    Source.DEVOPS_AGENT: devops_agent_event,
    Source.KIRO: kiro_session,
    Source.MERGE: merged_pull_request,
    Source.SELF_DECLARED: declared_episode,
    Source.PORTAL: declared_episode,
}


def trace_from_payload(
    source: Source, payload: Mapping[str, Any], model: ModelBackend | None = None
) -> Trace:
    """Normalise the payload of one source; the entry point of ``ingest_raw``."""
    adapter = ADAPTERS.get(source)
    if adapter is None:
        raise ValidationError(f"source sans adaptateur de capture : {source}", source=source.value)
    return adapter(payload, model)


# -- shared small builders -------------------------------------------------- #
def _trace(
    source: Source,
    detail: Mapping[str, Any],
    events: list[TraceEvent],
    external_key: str,
    extra_origin: Mapping[str, str] | None = None,
) -> Trace:
    """Assemble the trace of an adapter, with the attributes of its payload."""
    origin = _origin(detail, ("titre", "title"), ("ticket", "ticket"))
    origin.update({key: str(value) for key, value in (detail.get("origin") or {}).items()})
    origin.update(dict(extra_origin or {}))
    return build_trace(
        source=source,
        scope=scope_of(detail),
        events=events,
        external_id=str(detail.get(external_key) or detail.get("id") or ""),
        author=str(detail.get("author") or detail.get("resolver") or ""),
        technologies=detail.get("technologies") or detail.get("technos") or (),
        closed_at=parse_moment(_ended(detail)),
        do_not_distill=bool(detail.get("do_not_distill", False)),
        origin=origin,
        verdict=verdict_of(detail),
    )


def _steps(detail: Mapping[str, Any], source: Source, model: ModelBackend | None) -> list[TraceEvent]:
    """The steps of a session: one action, and the tool output that answered it."""
    events = []
    for step in detail.get("steps", ()) or ():
        if not isinstance(step, Mapping):
            continue
        kind = _kind_of(step)
        identity = str(step.get("actor") or step.get("author") or "")
        events.append(
            event_of(
                {
                    "text": str(step.get("text") or ""),
                    "identity": identity,
                    "author_kind": kind,
                    "at": step.get("at"),
                    "kind": str(step.get("kind") or "action"),
                },
                source,
                model,
            )
        )
        output = str(step.get("tool_output") or step.get("toolOutput") or "")
        if output:
            events.append(_tool(output, step, source, kind))
    return events


def _kind_of(step: Mapping[str, Any]) -> str:
    """Who acted in one step: an agent by default, a human when said so."""
    return str(step.get("actor_kind") or step.get("actorKind") or step.get("author_kind") or "agent")


def _attempts(detail: Mapping[str, Any]) -> list[TraceEvent]:
    """The attempts of a declared episode: action, reasoning, result.

    An attempt that names nobody is the declaring agent's: a self declared
    episode has one author, and every element of it must carry that author.
    """
    declarer = {"actor": str(detail.get("author") or detail.get("agent") or "")}
    events = []
    for attempt in detail.get("attempts", ()) or ():
        if not isinstance(attempt, Mapping):
            continue
        step = {**declarer, **{key: value for key, value in attempt.items() if value}}
        text = str(step.get("action") or "")
        reasoning = str(step.get("reasoning") or "")
        if reasoning:
            text = f"{text} ({reasoning})" if text else reasoning
        events.append(_action(text, step, AuthorKind.AGENT, Source.SELF_DECLARED))
        result = str(step.get("result") or "")
        if result:
            events.append(_tool(result, step, Source.SELF_DECLARED, "agent"))
    return events


def _evidence(detail: Mapping[str, Any], model: ModelBackend | None) -> list[TraceEvent]:
    """The evidence an agent quotes, reduced to its useful lines."""
    text = summarise_logs(str(detail.get("evidence") or ""), model)
    if not text:
        return []
    step = {"actor": str(detail.get("author") or ""), "actor_kind": "agent"}
    return [_tool(text, step, Source.SELF_DECLARED, "agent")]


def _reviews(detail: Mapping[str, Any]) -> list[TraceEvent]:
    """The review exchanges of a merged correction: a verdict per reviewer."""
    events = []
    for review in detail.get("reviews", ()) or ():
        if not isinstance(review, Mapping):
            continue
        state = str(review.get("state") or "commenté")
        body = f"Relecture ({state}) : {review.get('text') or ''}".strip()
        events.append(
            TraceEvent(
                role=Role.OTHER,
                text=f"{LABEL_SYSTEM} {body}",
                author=pseudonym(str(review.get("author") or SYSTEM_IDENTITY)),
                author_kind=AuthorKind.HUMAN,
                at=parse_moment(review.get("at")),
                kind="system",
            )
        )
    return events


def _request(text: str, detail: Mapping[str, Any]) -> TraceEvent:
    """The element that opens the work: an alarm, a request, a description.

    An alarm is asked by the monitoring, not by a person: its author kind says
    so, and the shares of EF-CAP-09 are not polluted by it.
    """
    alarm = str(detail.get("trigger", "")).lower() in ALARM_TRIGGERS
    fallback = SYSTEM_IDENTITY if alarm else str(detail.get("author") or SYSTEM_IDENTITY)
    identity = str(detail.get("requester") or fallback)
    started = detail.get("startedAt") or detail.get("created_at") or detail.get("createdAt")
    return TraceEvent(
        role=Role.USER,
        text=f"{LABEL_ALARM if alarm else LABEL_HUMAN} {str(text).strip()}",
        author=pseudonym(identity),
        author_kind=AuthorKind.AGENT if alarm else AuthorKind.HUMAN,
        at=parse_moment(started),
        kind="request",
    )


def _action(text: str, step: Mapping[str, Any], kind: AuthorKind, source: Source) -> TraceEvent:
    """One action of the one who resolves - human or agent, same role (13.1)."""
    return event_of(
        {
            "text": str(text or ""),
            "identity": str(step.get("actor") or step.get("author") or ""),
            "author_kind": kind.value,
            "at": step.get("at"),
            "kind": "action",
        },
        source,
    )


def _tool(output: str, step: Mapping[str, Any], source: Source, kind: str) -> TraceEvent:
    """One tool output, kept because it improves the results (13.1)."""
    return event_of(
        {
            "text": output,
            "label": LABEL_COMMAND,
            "identity": str(step.get("actor") or step.get("author") or SYSTEM_IDENTITY),
            "author_kind": kind,
            "at": step.get("at"),
            "kind": "tool_output",
        },
        source,
    )


def _system(body: str, at: datetime | None) -> TraceEvent:
    """A system event: the explicit closing of the work (13.1)."""
    return TraceEvent(
        role=Role.OTHER,
        text=f"{LABEL_SYSTEM} {body}",
        author=pseudonym(SYSTEM_IDENTITY),
        author_kind=AuthorKind.AGENT,
        at=at,
        kind="system",
    )


def _ended(detail: Mapping[str, Any], key: str = "endedAt") -> Any:
    """The instant the work ended, under whichever name the source sends it."""
    return detail.get(key) or detail.get("closedAt") or detail.get("closed_at") or detail.get("at")


def _guichet_origin(payload: Mapping[str, Any]) -> dict[str, str]:
    """Catalogue attributes of a ticket: its number, its code, its title."""
    origin = _origin(payload, ("code", "code"), ("titre", "title"))
    ticket = str(payload.get("ticket") or payload.get("id") or "")
    if ticket:
        origin["ticket"] = ticket
    return origin


def _origin(detail: Mapping[str, Any], *pairs: tuple[str, str]) -> dict[str, str]:
    """Catalogue attributes read in a payload, omitted when absent."""
    origin: dict[str, str] = {}
    for name, key in pairs:
        value = detail.get(key)
        if value:
            origin[name] = str(value)
    return origin


__all__ = [
    "ADAPTERS",
    "DECLARED_OUTCOME",
    "DECLARED_REQUIRED",
    "Adapter",
    "declared_episode",
    "devops_agent_event",
    "guichet_ticket",
    "kiro_session",
    "merged_pull_request",
    "trace_from_payload",
]
