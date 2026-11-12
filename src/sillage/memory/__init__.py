"""Memory: the episodes, the lessons and the fiches of Sillage.

Public surface of the module. Everything imported here is offline and
deterministic: no AWS import, no network, no model call at import time, and no
figure written by a model (ENF-IA-01).

Sub-modules:

* :mod:`sillage.memory.namespaces` - the spaces of chapter 12.5, and the rule of
  chapter 10.4 that computes them from the token and from nothing else;
* :mod:`sillage.memory.tfidf` - the offline semantic search: tf-idf and cosine,
  French and English stop words, deterministic;
* :mod:`sillage.memory.local_records` - what the offline backend stores, and how
  a trace is cut into batches of 100 events;
* :mod:`sillage.memory.local` - ``LocalMemoryBackend``, the offline twin of
  AgentCore Memory, which replaces the capture's fallback in the real wiring;
* :mod:`sillage.memory.grouping` - how the episodes of one perimeter are grouped
  before a lesson is drawn from them;
* :mod:`sillage.memory.distill` - the episode of one job, and the lessons of one
  perimeter, never of two (EF-DIS-01 to EF-DIS-07);
* :mod:`sillage.memory.search` - the reformulation into an intention, the search
  and the ranking of chapter 13.4 (EF-RES-04, EF-RES-09);
* :mod:`sillage.memory.agentcore` - ``AgentCoreMemoryBackend``, the deployed
  twin, imported **on demand** because it is the only one that needs boto3.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from sillage.memory.distill import (
    MIN_EPISODES_FOR_A_LESSON,
    confidence_of,
    distil_episode,
    episode_content,
    failed_paths_of,
    lesson_from_records,
    reflect,
    teachings_of,
)
from sillage.memory.grouping import (
    SIMILARITY_THRESHOLD,
    TECHNOLOGY_FAMILIES,
    family_of,
)
from sillage.memory.local import LocalMemoryBackend
from sillage.memory.local_records import StoredEvent
from sillage.memory.namespaces import (
    common_namespace,
    episode_namespace,
    fiche_namespace,
    namespace_of_token,
    namespaces_of_scope,
    reflection_namespace,
    scope_of_actor,
)
from sillage.memory.search import (
    INTENT_PATTERNS,
    LEVEL_ORDER,
    SearchHit,
    hit_of,
    query_namespaces,
    rank,
    rewrite_intent,
    scope_of_namespace,
    search,
)
from sillage.memory.tfidf import STOPWORDS, SemanticIndex, fold, tokenize

#: Modules imported only when one of their names is asked for.
_LAZY: dict[str, str] = {
    "AgentCoreMemoryBackend": "sillage.memory.agentcore",
}


def __getattr__(name: str) -> Any:
    """Import the AWS backend on demand: boto3 is never imported for nothing."""
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module_name), name)


def __dir__() -> list[str]:
    """The names of the module, the lazy ones included."""
    return sorted([*globals(), *_LAZY])


__all__ = [
    "INTENT_PATTERNS",
    "LEVEL_ORDER",
    "MIN_EPISODES_FOR_A_LESSON",
    "SIMILARITY_THRESHOLD",
    "STOPWORDS",
    "TECHNOLOGY_FAMILIES",
    "AgentCoreMemoryBackend",
    "LocalMemoryBackend",
    "SearchHit",
    "SemanticIndex",
    "StoredEvent",
    "common_namespace",
    "confidence_of",
    "distil_episode",
    "episode_content",
    "episode_namespace",
    "failed_paths_of",
    "family_of",
    "fiche_namespace",
    "fold",
    "hit_of",
    "lesson_from_records",
    "namespace_of_token",
    "namespaces_of_scope",
    "query_namespaces",
    "rank",
    "reflect",
    "reflection_namespace",
    "rewrite_intent",
    "scope_of_actor",
    "scope_of_namespace",
    "search",
    "teachings_of",
    "tokenize",
]
