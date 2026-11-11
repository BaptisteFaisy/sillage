"""Shared sample objects of the catalogue tests: one factory per entity.

They are plain functions, not fixtures, so a test reads as one line: ``make_fiche(
scope_a, level=Level.PROVEN)``. Every sample carries a perimeter, a pseudonymous
author and a date, because that is what the catalogue keys and sorts on.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from sillage.kernel.types import (
    AuthorKind,
    Claim,
    Episode,
    Fiche,
    Lesson,
    Level,
    Outcome,
    Scope,
    ScopeKind,
    Source,
    Trace,
    Turn,
)

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
COMMON = Scope(kind=ScopeKind.COMMON)


def make_trace(scope: Scope, work_item_id: str = "guichet:2041", **changes) -> Trace:
    """A finished job of one perimeter, as capture would store it."""
    base = Trace(
        work_item_id=work_item_id,
        scope=scope,
        source=Source.GUICHET,
        author_kind=AuthorKind.HUMAN,
        author="p-7f3a",
        technologies=("postgresql", "ecs"),
        closed_at=NOW,
        received_at=NOW,
        origin={"ticket": work_item_id},
    )
    return replace(base, **changes)


def make_episode(scope: Scope, episode_id: str = "ep-1", work_item_id: str = "guichet:2041", **changes):
    base = Episode(
        episode_id=episode_id,
        scope=scope,
        work_item_id=work_item_id,
        situation="500 sur le site apres un deploiement",
        intent="retablir le service",
        turns=(Turn("redemarrer", "base saturee", "aucun effet"),),
        outcome=Outcome.SUCCESS,
        author="p-7f3a",
        created_at=NOW,
    )
    return replace(base, **changes)


def make_lesson(scope: Scope, lesson_id: str = "le-1", episode_ids: tuple[str, ...] = ("ep-1",), **changes):
    base = Lesson(
        lesson_id=lesson_id,
        scope=scope,
        use_case="connexions epuisees apres un deploiement",
        advice=("verifier le pool de connexions",),
        pitfalls=("redemarrer sans verifier",),
        confidence=0.7,
        episode_ids=episode_ids,
        created_at=NOW,
    )
    return replace(base, **changes)


def make_fiche(scope: Scope, fiche_id: str = "f-1", **changes) -> Fiche:
    base = Fiche(
        fiche_id=fiche_id,
        scope=scope,
        title="Connexions a la base epuisees",
        body="Le pool de connexions a ete porte de 10 a 50 par instance au deploiement.",
        steps=("verifier le pool", "revenir a 10 par instance"),
        avoid=("redemarrer sans verifier",),
        validity="postgresql 14, ecs",
        claims=(Claim("le pool est trop grand", ("ep-1",)),),
        sources=("guichet:2041",),
        level=Level.VALIDATED,
        validated_by="p-referent",
        validated_at=NOW,
    )
    return replace(base, **changes)
