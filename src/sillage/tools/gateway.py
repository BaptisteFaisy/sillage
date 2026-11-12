"""What every tool does before it works: the caller, the policy, the perimeter.

The three rules of chapter 10.4 live here, once, and every tool goes through
them:

* the **caller** comes from a token, never from the body of the call
  (:func:`caller_of` builds the demo default, the gateway passes the real one);
* the **decision** is taken by layer 1 before any logic runs
  (:func:`enforce`), and a refusal raises :class:`PolicyDenied` that the tool
  turns into a payload;
* the **namespace** interrogated is computed from the token
  (:func:`namespaces_for`), never from a free text: an agent of client B that
  writes "client A" in its question still reads the spaces of client B.

The module also holds the small shared reads - the object a memory record
holds, the fiche a search found - and the one write every tool shares: a
fiche is never edited in place, it is versioned (EF-ENT-04).
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from typing import Any

from sillage.catalog.versions import bump
from sillage.kernel.config import Settings
from sillage.kernel.errors import PolicyDenied, ValidationError
from sillage.kernel.ids import pseudonym
from sillage.kernel.ids import usage_id as make_usage_id
from sillage.kernel.protocols import CatalogStore, Principal, ToolRequest
from sillage.kernel.types import (
    Decision,
    Episode,
    FeedbackResult,
    Fiche,
    Lesson,
    PrincipalRole,
    Scope,
    ScopeKind,
)
from sillage.memory.namespaces import common_namespace, namespace_of_token, namespaces_of_scope

#: The role a tool assumes when the caller gives none: an engineer of the client.
DEFAULT_ROLE = PrincipalRole.ENGINEER

#: The spellings of a usage feedback, in French and in English (EF-RET-02).
FEEDBACK_RESULTS: dict[str, FeedbackResult] = {
    "helped": FeedbackResult.HELPED,
    "a_aide": FeedbackResult.HELPED,
    "a aidé": FeedbackResult.HELPED,
    "aide": FeedbackResult.HELPED,
    "did_not_help": FeedbackResult.DID_NOT_HELP,
    "n_a_pas_aide": FeedbackResult.DID_NOT_HELP,
    "n'a pas aidé": FeedbackResult.DID_NOT_HELP,
    "pas_aide": FeedbackResult.DID_NOT_HELP,
    "wrong": FeedbackResult.WRONG,
    "faux": FeedbackResult.WRONG,
    "etait_faux": FeedbackResult.WRONG,
    "était faux": FeedbackResult.WRONG,
}


def caller_of(
    principal: Principal | None,
    *,
    client_id: str = "",
    role: PrincipalRole = DEFAULT_ROLE,
    mode: Any = None,
) -> Principal:
    """The caller of a call: the one the caller gave, or the demo default.

    The gateway always passes the principal it built from the token. The default
    exists for the smoke, the demo and the tests: it is the engineer of the
    client the call names, which is the shape of a legitimate consultation.
    """
    if principal is not None:
        return principal
    subject = f"demo-{role.value}-{client_id or 'msp'}"
    return Principal(subject=subject, role=role, client_id=client_id or None, mode=mode)


def request_of(tool: str, principal: Principal, **arguments: Any) -> ToolRequest:
    """The call as the gateway sees it: a tool, a principal, and arguments."""
    return ToolRequest(tool=tool, principal=principal, arguments=dict(arguments))


def enforce(stack: Any, tool: str, principal: Principal, **arguments: Any) -> None:
    """Ask layer 1 before any logic; a refusal raises :class:`PolicyDenied`.

    The engine decides on the token and on the parameters of the call only, and
    it writes the decision in the journal whatever it is (ENF-SEC-05). The tool
    never decides anything itself: it either goes on, or it returns the refusal.
    """
    request = request_of(tool, principal, **arguments)
    engine = stack.policy
    guard = getattr(engine, "enforce", None)
    if callable(guard):
        guard(request)
        return
    authorization = engine.authorize(request)
    if authorization.decision is not Decision.PERMIT:
        raise PolicyDenied(
            f"{tool} refusé par la politique {authorization.rule} : {authorization.reason}",
            rule=authorization.rule,
            tool=tool,
        )


def perimeter_of(principal: Principal, client_id: str = "") -> Scope:
    """The perimeter a call reads: the client of the token, else the one it names.

    A token that carries a client is never widened by the body of the call. A
    caller the token does not bind to a client - an administrator, a team lead -
    reads the perimeter its call names: the tool is what controls the clients a
    human may reach (specification 12.6).
    """
    home = principal.home_scope()
    if home is not None:
        return home
    if client_id:
        return Scope(kind=ScopeKind.CLIENT, client_id=client_id)
    return Scope(kind=ScopeKind.INTERNAL)


def namespaces_for(settings: Settings, principal: Principal, client_id: str = "") -> tuple[str, ...]:
    """The namespaces a caller may read, computed from its token (spec. 10.4).

    The home perimeter of the token comes first, the common knowledge last. A
    caller the token does not bind to a client reads the perimeter its call
    names; a caller the token binds to a client never leaves it.
    """
    allowed = namespace_of_token(settings, principal)
    if principal.client_id or not client_id:
        return allowed
    perimeter = Scope(kind=ScopeKind.CLIENT, client_id=client_id)
    return (*namespaces_of_scope(settings, perimeter), common_namespace(settings))


def now_of(stack: Any) -> datetime:
    """The instant of the stack: its clock when it has one, the machine clock else."""
    clock = getattr(stack, "now", None)
    if callable(clock):
        return clock()
    return datetime.now(UTC)


def writer_of(principal: Principal) -> str:
    """The caller as the catalogue writes it: a pseudonym, never an identity."""
    return pseudonym(principal.subject)


#: A counter that keeps two identical consultations apart: each one is a row.
_CALLS = count(1)


def usage_identifier(caller: Principal, moment: datetime, problem: str) -> str:
    """The identifier of one consultation: two consultations never share one.

    The kernel builds it from the caller, the instant and the problem; the
    counter adds what two identical consultations of the same instant would
    otherwise share, because each consultation is a usage row of its own
    (EF-RET-01) and a feedback attaches to one of them.
    """
    return make_usage_id(caller.subject, moment.isoformat(), f"{problem}#{next(_CALLS)}")


def feedback_result_of(value: str) -> FeedbackResult:
    """The feedback a caller means, in French or in English (EF-RET-02)."""
    key = str(value or "").strip().lower()
    found = FEEDBACK_RESULTS.get(key)
    if found is None:
        raise ValidationError(
            f"retour d'usage inconnu : {value!r} (attendu : a aidé, n'a pas aidé, était faux)",
            result=str(value or ""),
        )
    return found


# -- the object a memory record holds --------------------------------------- #
def fiche_of(catalog: CatalogStore, record_id: str) -> Fiche | None:
    """The fiche a memory record holds the approved text of."""
    for fiche in catalog.list_fiches():
        if fiche.memory_record_id == record_id or fiche.fiche_id == record_id:
            return fiche
    return None


def lesson_of(catalog: CatalogStore, record_id: str) -> Lesson | None:
    """The lesson a memory record holds."""
    for lesson in catalog.list_lessons():
        if lesson.memory_record_id == record_id or lesson.lesson_id == record_id:
            return lesson
    return None


def episode_of(catalog: CatalogStore, record_id: str) -> Episode | None:
    """The episode a memory record holds."""
    for episode in catalog.list_episodes():
        if episode.memory_record_id == record_id or episode.episode_id == record_id:
            return episode
    return None


# -- the one write every tool shares ---------------------------------------- #
def store_fiche(stack: Any, fiche: Fiche, **changes: Any) -> Fiche:
    """Write a fiche as a new version: a change never overwrites its predecessor.

    Versioning is not a nicety here (EF-ENT-04): a counter that moved, a level
    that changed and a corrected text are three different states, and a reader
    of the history must be able to tell them apart.
    """
    updated = bump(fiche, **changes)
    stack.catalog.put_fiche(updated)
    return updated


def create_fiche(stack: Any, fiche: Fiche) -> Fiche:
    """Write a fiche that did not exist yet: its first version."""
    stack.catalog.put_fiche(fiche)
    return fiche


__all__ = [
    "DEFAULT_ROLE",
    "FEEDBACK_RESULTS",
    "caller_of",
    "create_fiche",
    "enforce",
    "episode_of",
    "feedback_result_of",
    "fiche_of",
    "lesson_of",
    "namespaces_for",
    "now_of",
    "perimeter_of",
    "request_of",
    "store_fiche",
    "writer_of",
]
