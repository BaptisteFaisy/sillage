"""The search: reformulate, look in the right spaces, join with the catalogue.

Chapter 13.4 of the specification, step by step:

1. a fast model reformulates the problem as an **intention** - episodes are
   indexed on their intention and lessons on their use case, so a raw error
   message finds almost nothing (EF-RES-09);
2. the lessons and the fiches of the perimeter are searched, then the common
   knowledge;
3. the close episodes of the perimeter are searched;
4. the catalogue gives the trust level, the success rate and the freshness of
   each hit - three figures computed by code, never by a model (ENF-IA-01);
5. the hits are ranked: the perimeter before the common knowledge, proven before
   validated, then the success rate and the freshness (EF-RES-04).

The spaces searched are the ones :mod:`sillage.memory.namespaces` computes from
the perimeter of the caller: a search never reaches a namespace the token does
not carry (specification 10.4).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sillage.catalog.scoring import NEUTRAL_RATE, freshness_days, success_rate
from sillage.kernel.config import DEFAULT, Settings
from sillage.kernel.protocols import CatalogStore, MemoryBackend, MemoryRecord, ModelBackend
from sillage.kernel.types import SERVED_TO_HUMAN, Level, ModelTier, Scope, ScopeKind
from sillage.memory.namespaces import common_namespace, namespaces_of_scope, scope_of_actor
from sillage.memory.tfidf import raw_tokens, tokenize

#: The perimeter carried by a namespace: ``/actor/client-a/`` or ``/fiches/client-a/``.
_SCOPE_IN_NAMESPACE = re.compile(r"/(?:actor|fiches)/(?P<scope>[^/]+)/")

#: Freshness of a hit that was never confirmed: it sorts after every dated one.
_NEVER_CONFIRMED = 10**6

#: The intention of a well known problem, in the order the patterns are tried.
INTENT_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("too", "many", "connections"), "Rétablir un service qui épuise les connexions à sa base"),
    (("connexions", "base"), "Rétablir un service qui épuise les connexions à sa base"),
    (("pool", "connexions"), "Rétablir un service dont le groupe de connexions est mal réglé"),
    (("erreur", "500"), "Rétablir un service web qui renvoie des erreurs 500"),
    (("502",), "Rétablir un service web que son mandataire ne joint plus"),
    (("timeout",), "Rétablir un service qui expire sur ses appels"),
    (("deploiement",), "Rétablir un service après un déploiement"),
    (("memoire", "saturee"), "Rétablir un service dont la mémoire est saturée"),
)

#: The order of the trust levels in a ranking: proven first, withdrawn last.
LEVEL_ORDER: dict[Level, int] = {
    Level.PROVEN: 0,
    Level.VALIDATED: 1,
    Level.PROPOSED: 2,
    Level.RAW: 3,
    Level.SET_ASIDE: 4,
    Level.WITHDRAWN: 5,
}


@dataclass(frozen=True)
class SearchHit:
    """One record found, with everything the catalogue says about it."""

    record_id: str
    namespace: str
    content: str
    score: float
    level: Level
    scope: Scope
    success_rate: float = NEUTRAL_RATE
    freshness_days: int | None = None


# -- the reformulation ------------------------------------------------------ #
def rewrite_intent(problem: str, model: ModelBackend | None) -> str:
    """Reformulate a problem as an intention (EF-RES-09).

    With a model, the answer of the model is the intention. Without one - the
    offline tests, the demo, a Bedrock outage - a deterministic rule takes over:
    a known pattern gives its intention, anything else keeps its meaningful words
    and drops the noise. The reformulation never returns the raw text unchanged
    when it has something better to say.
    """
    if model is not None:
        answer = _ask_model(model, problem)
        if answer:
            return answer
    return _deterministic_intent(problem)


def _ask_model(model: ModelBackend, problem: str) -> str:
    """One short question to a fast model: the intention, and nothing else."""
    prompt = (
        "Reformule le problème d'exploitation suivant en une seule phrase d'intention, "
        "sans chiffre, sans identifiant de client, en français.\n\n"
        f"Problème : {problem}"
    )
    try:
        answer = model.complete(
            prompt=prompt,
            system="Tu reformules un problème en intention. Une phrase, aucun chiffre.",
            tier=ModelTier.FAST,
        )
    except Exception:  # a model that fails must not fail the search
        return ""
    return (answer or "").strip()


def _deterministic_intent(problem: str) -> str:
    """The intention a rule can give without a model."""
    for pattern in INTENT_PATTERNS:
        if _matches(problem, pattern[0]):
            return pattern[1]
    words = list(dict.fromkeys(tokenize(problem)))
    if not words:
        return (problem or "").strip()
    return " ".join(words[:12])


def _matches(problem: str, needles: tuple[str, ...]) -> bool:
    """True when every folded word of a pattern appears in the problem."""
    words = set(raw_tokens(problem))
    return all(needle in words for needle in needles)


# -- the search ------------------------------------------------------------- #
def search(
    settings: Settings,
    memory: MemoryBackend,
    catalog: CatalogStore | None,
    *,
    scope: Scope,
    query: str,
    top_k: int = 5,
    model: ModelBackend | None = None,
    now: datetime | None = None,
) -> tuple[SearchHit, ...]:
    """What one perimeter may read about a problem, best first.

    The spaces searched are computed from the perimeter, never from the query:
    the strategy space of the perimeter (its lessons and its episodes), its
    fiches, and the common knowledge. Every hit is joined with the catalogue, so
    its level, its success rate and its freshness are the ones the code computed.
    ``model`` and ``now`` are optional: without a model the reformulation falls
    back on its deterministic rule, without a date the freshness is read now.
    """
    intent = rewrite_intent(query, model)
    moment = now or datetime.now(UTC)
    hits: list[SearchHit] = []
    for namespace in query_namespaces(settings, scope):
        for record in memory.retrieve(namespace=namespace, query=intent, top_k=top_k):
            hits.append(hit_of(record, catalog, moment))
    ranked = rank(hits, scope_first=scope, levels=SERVED_TO_HUMAN)
    return ranked[: max(int(top_k), 0)]


def query_namespaces(settings: Settings = DEFAULT, scope: Scope | None = None) -> tuple[str, ...]:
    """The namespaces one search covers: the perimeter first, the common last.

    The spaces of the perimeter are the ones :func:`namespaces_of_scope` computes,
    so a search and a token agree on what a perimeter owns - one implementation
    of the rule, not two.
    """
    common = common_namespace(settings)
    if scope is None:
        return (common,)
    found: list[str] = []
    for namespace in (*namespaces_of_scope(settings, scope), common):
        if namespace not in found:
            found.append(namespace)
    return tuple(found)


def hit_of(record: MemoryRecord, catalog: CatalogStore | None, now: datetime) -> SearchHit:
    """One memory record, joined with the row the catalogue holds for it."""
    fiche = _fiche_of(catalog, record.record_id)
    if fiche is not None:
        return SearchHit(
            record_id=record.record_id,
            namespace=record.namespace,
            content=record.content,
            score=record.score,
            level=fiche.level,
            scope=fiche.scope,
            success_rate=success_rate(successes=fiche.success_count, failures=fiche.failure_count),
            freshness_days=freshness_days(fiche, now),
        )
    row = _lesson_of(catalog, record.record_id) or _episode_of(catalog, record.record_id)
    if row is not None:
        return SearchHit(
            record_id=record.record_id,
            namespace=record.namespace,
            content=record.content,
            score=record.score,
            level=row.level,
            scope=row.scope,
            freshness_days=_age(row.created_at, now),
        )
    return SearchHit(
        record_id=record.record_id,
        namespace=record.namespace,
        content=record.content,
        score=record.score,
        level=Level.RAW,
        scope=scope_of_namespace(record.namespace),
        freshness_days=_age(record.created_at, now),
    )


def scope_of_namespace(namespace: str) -> Scope:
    """The perimeter a namespace belongs to, read in the namespace itself."""
    match = _SCOPE_IN_NAMESPACE.search(namespace or "")
    if match is None:
        return Scope(kind=ScopeKind.COMMON)
    return scope_of_actor(match.group("scope"))


# -- the ranking ------------------------------------------------------------ #
def rank(
    hits: Sequence[SearchHit], *, scope_first: Scope, levels: Collection[Level]
) -> tuple[SearchHit, ...]:
    """Rank the hits the way chapter 13.4 says, and drop what may not be served.

    The perimeter of the caller comes before the common knowledge, a proven fiche
    before a validated one, then the success rate and the freshness. The semantic
    score only breaks the remaining ties: it says which of two equally trusted
    records is closer to the intention, never which of them may be served
    (EF-RES-04, EF-RES-05).
    """
    kept = [hit for hit in hits if hit.level in levels]
    kept.sort(key=lambda hit: _rank_key(hit, scope_first))
    return tuple(kept)


def _rank_key(hit: SearchHit, scope_first: Scope) -> tuple:
    """The sort key of one hit, in the order of the specification."""
    return (
        0 if hit.scope.actor_id == scope_first.actor_id else 1,
        LEVEL_ORDER.get(hit.level, len(LEVEL_ORDER)),
        -hit.success_rate,
        hit.freshness_days if hit.freshness_days is not None else _NEVER_CONFIRMED,
        -hit.score,
        hit.record_id,
    )


# -- catalogue lookups ------------------------------------------------------ #
def _fiche_of(catalog: CatalogStore | None, record_id: str):
    """The fiche a memory record holds the approved text of."""
    if catalog is None:
        return None
    for fiche in catalog.list_fiches():
        if fiche.memory_record_id == record_id:
            return fiche
    return None


def _lesson_of(catalog: CatalogStore | None, record_id: str):
    """The lesson a memory record holds."""
    if catalog is None:
        return None
    for lesson in catalog.list_lessons():
        if lesson.memory_record_id == record_id:
            return lesson
    return None


def _episode_of(catalog: CatalogStore | None, record_id: str):
    """The episode a memory record holds."""
    if catalog is None:
        return None
    for episode in catalog.list_episodes():
        if episode.memory_record_id == record_id:
            return episode
    return None


def _age(moment: datetime | None, now: datetime) -> int | None:
    """Whole days since an instant, ``None`` when the row carries no date."""
    if moment is None:
        return None
    earlier = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return max(0, (now - earlier).days)


__all__ = [
    "INTENT_PATTERNS",
    "LEVEL_ORDER",
    "SearchHit",
    "hit_of",
    "query_namespaces",
    "rank",
    "rewrite_intent",
    "scope_of_namespace",
    "search",
]
