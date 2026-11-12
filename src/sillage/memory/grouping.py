"""Grouping the episodes of one perimeter: the reflection, without the service.

A reflection is what AgentCore Memory does server side. Offline it has to be
reproduced, and it has to be **deterministic**: two runs on the same episodes
give the same groups, in the same order, whatever the insertion order was.

Two rules decide what may be reflected on together:

* the **technological family** - an episode about a database and one about a
  reverse proxy never join the same lesson, even when they share a few words;
* the **similarity** of their text - a tf-idf cosine above
  :data:`SIMILARITY_THRESHOLD`, measured against the episodes already in the
  group, never against a single one of them.

The grouping is greedy and walks the items in their own order, so the answer is
reproducible; it is also stateless - every item carries its own object, and no
module level registry is kept between two reflections.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sillage.kernel.protocols import MemoryRecord
from sillage.kernel.types import Episode
from sillage.memory.tfidf import SemanticIndex

#: Cosine above which two episodes are considered close enough to be reflected on.
SIMILARITY_THRESHOLD = 0.12

#: Technology families: two episodes of another family never join a lesson.
TECHNOLOGY_FAMILIES: dict[str, str] = {
    "postgresql": "database",
    "postgres": "database",
    "mysql": "database",
    "mariadb": "database",
    "mongodb": "database",
    "redis": "cache",
    "memcached": "cache",
    "elasticsearch": "search",
    "opensearch": "search",
    "ecs": "orchestration",
    "eks": "orchestration",
    "kubernetes": "orchestration",
    "docker": "container",
    "nginx": "proxy",
    "haproxy": "proxy",
    "envoy": "proxy",
    "kafka": "messaging",
    "rabbitmq": "messaging",
    "sqs": "messaging",
    "lambda": "function",
}

#: Family of a job that declares no technology.
UNKNOWN_FAMILY = "unknown"


@dataclass(frozen=True)
class Item:
    """One thing to reflect on: an episode or a record, seen the same way.

    ``payload`` keeps the object itself, so a grouping never reaches for a module
    level registry and two reflections can run side by side.
    """

    key: str
    text: str
    family: str
    payload: object


def family_of(technologies: Sequence[str]) -> str:
    """The technological family of a job: the family of its first technology."""
    for technology in technologies:
        folded = (technology or "").strip().lower()
        if folded:
            return TECHNOLOGY_FAMILIES.get(folded, folded)
    return UNKNOWN_FAMILY


def item_of_episode(episode: Episode) -> Item:
    """An episode, seen as something to reflect on."""
    text = " ".join([episode.situation, episode.intent, *(turn.action for turn in episode.turns)])
    return Item(
        key=episode.episode_id,
        text=text,
        family=family_of(episode.technologies),
        payload=episode,
    )


def item_of_record(record: MemoryRecord) -> Item:
    """A record, seen as something to reflect on."""
    technologies = [part for part in (record.metadata.get("techno", "") or "").split(",") if part.strip()]
    return Item(
        key=record.record_id,
        text=record.content,
        family=family_of(technologies),
        payload=record,
    )


def cluster(items: Sequence[Item]) -> tuple[tuple[Item, ...], ...]:
    """Group the items by technology family, then by similarity."""
    by_family: dict[str, list[Item]] = {}
    for item in items:
        by_family.setdefault(item.family, []).append(item)
    groups: list[list[Item]] = []
    for family in sorted(by_family):
        groups.extend(_cluster_family(by_family[family]))
    return tuple(tuple(group) for group in groups)


def episodes_of(group: Sequence[Item]) -> list[Episode]:
    """The episodes a group of items carries."""
    return [item.payload for item in group if isinstance(item.payload, Episode)]


def records_of(group: Sequence[Item]) -> list[MemoryRecord]:
    """The records a group of items carries."""
    return [item.payload for item in group if isinstance(item.payload, MemoryRecord)]


def _cluster_family(items: Sequence[Item]) -> list[list[Item]]:
    """Cluster the items of one family: greedy, in their own order."""
    index = SemanticIndex()
    for item in items:
        index.index(item.key, item.text)
    groups: list[list[Item]] = []
    for item in items:
        position, score = _best_group(index, groups, item)
        if position >= 0 and score >= SIMILARITY_THRESHOLD:
            groups[position].append(item)
        else:
            groups.append([item])
    return groups


def _best_group(index: SemanticIndex, groups: Sequence[list[Item]], item: Item) -> tuple[int, float]:
    """The group an item joins, and how close it is to it."""
    position, best = -1, 0.0
    for number, group in enumerate(groups):
        score = _affinity(index, group, item)
        if score > best:
            position, best = number, score
    return position, best


def _affinity(index: SemanticIndex, group: Sequence[Item], item: Item) -> float:
    """How close an item is to a group: the mean of its closeness to each member."""
    scores = [index.similarity(item.key, member.key) for member in group]
    return sum(scores) / len(scores) if scores else 0.0


__all__ = [
    "SIMILARITY_THRESHOLD",
    "TECHNOLOGY_FAMILIES",
    "UNKNOWN_FAMILY",
    "Item",
    "cluster",
    "episodes_of",
    "family_of",
    "item_of_episode",
    "item_of_record",
    "records_of",
]
