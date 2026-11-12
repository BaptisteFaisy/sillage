"""The memory namespaces, computed by code - and by the token alone.

Chapter 12.5 of the specification makes one design choice that everything else
follows: **the actor is the perimeter, not the person.** An actor id is
``client-<id>``, ``interne`` or ``commun``, so a reflection crosses every job of
one client and never two clients, and a lesson of the common space cannot lead
back to the episode it came from (EF-GEN-07, R15).

Chapter 10.4 adds the second rule, the one this module exists to enforce: *the
memory namespace a caller may read is computed from its token, never from a free
text.* :func:`namespace_of_token` therefore takes a ``Principal`` and nothing
else - an agent of client B that writes "client A" in its query still gets the
namespaces of client B and of the common knowledge, because no string of the
request ever reaches this code.

The templates themselves live in :mod:`sillage.kernel.config` (annex A); this
module only binds them to the domain vocabulary, so there is one implementation
of each shape.
"""

from __future__ import annotations

from sillage.kernel.config import DEFAULT, Settings
from sillage.kernel.protocols import Principal
from sillage.kernel.types import Scope, ScopeKind

#: The prefix an actor id of a client carries (``client-a``).
CLIENT_PREFIX = "client-"


def episode_namespace(settings: Settings, scope: Scope, work_item_id: str) -> str:
    """The namespace of one finished job: ``/strategy/<id>/actor/<a>/session/<w>/``."""
    return settings.episode_namespace(scope.actor_id, work_item_id)


def reflection_namespace(settings: Settings, scope: Scope) -> str:
    """The namespace of the lessons of a perimeter: ``/strategy/<id>/actor/<a>/``.

    It is also the prefix under which the episodes of that perimeter live, which
    is what lets one search cover both without ever leaving the client.
    """
    return settings.reflection_namespace(scope.actor_id)


def fiche_namespace(settings: Settings, scope: Scope) -> str:
    """The namespace where the approved text of a perimeter is written verbatim.

    A client or the MSP itself gets ``/fiches/<perimeter>/``; the common
    knowledge lives in its own space (``/commun/``), because nothing of a client
    may reach it without the explicit steps of chapter 13.6.
    """
    if scope.is_common:
        return settings.common_namespace()
    return settings.fiche_namespace(scope.actor_id)


def common_namespace(settings: Settings = DEFAULT) -> str:
    """The namespace of the shared knowledge: ``/commun/``."""
    return settings.common_namespace()


def scope_of_actor(actor_id: str) -> Scope:
    """The perimeter an actor id designates - the inverse of ``Scope.actor_id``."""
    if actor_id.startswith(CLIENT_PREFIX):
        return Scope(kind=ScopeKind.CLIENT, client_id=actor_id[len(CLIENT_PREFIX) :])
    if actor_id == Scope(kind=ScopeKind.INTERNAL).actor_id:
        return Scope(kind=ScopeKind.INTERNAL)
    return Scope(kind=ScopeKind.COMMON)


def namespaces_of_scope(settings: Settings, scope: Scope) -> tuple[str, ...]:
    """Every namespace one perimeter owns: its strategy space and its fiches."""
    if scope.is_common:
        return (common_namespace(settings),)
    return (reflection_namespace(settings, scope), fiche_namespace(settings, scope))


def namespace_of_token(settings: Settings, principal: Principal) -> tuple[str, ...]:
    """The namespaces a caller may read, computed from its token only (10.4).

    The answer is the home perimeter of the token - the client it carries, or the
    MSP when it carries none - plus the common knowledge, home first. No argument
    of this function is a free text, so nothing written in a query, a problem
    description or a ticket can widen the answer: an agent of client B never
    receives a namespace of client A (R9, R15).
    """
    found: list[str] = []
    for scope in principal.allowed_scopes():
        for namespace in namespaces_of_scope(settings, scope):
            if namespace not in found:
                found.append(namespace)
    return tuple(found)


__all__ = [
    "CLIENT_PREFIX",
    "common_namespace",
    "episode_namespace",
    "fiche_namespace",
    "namespace_of_token",
    "namespaces_of_scope",
    "reflection_namespace",
    "scope_of_actor",
]
