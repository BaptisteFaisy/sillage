"""Offline semantic search: tf-idf and cosine, standard library only.

AgentCore Memory indexes episodes on their **intention** and lessons on their
**use case** (specification 13.4). The offline backend must behave the same
way, or the tests would prove nothing about the deployed service.

The implementation is deliberately small and deterministic - no embedding, no
model, no network:

* the text is folded (lowercase, accents removed) and cut into words;
* the punctuation and the French and English stop words are dropped;
* a document is a bag of words, weighted by the inverse document frequency of
  the corpus;
* the score is the cosine between the query and the document.

Two runs on the same corpus always give the same ranking: ties are broken by
the record id, never by the insertion order.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable

#: One word of a folded text: letters and digits, nothing else.
_TOKEN = re.compile(r"[a-z0-9]+")

#: French stop words, as one text: the words that carry no operational meaning.
_FRENCH_STOP_WORDS = (
    "a à ai aient ainsi ait alors après as assez au aucun aura aurait aussi autre autres aux "
    "avant bien car ce cela ces cet cette ceux chaque comme comment dans de des donc dont du "
    "elle elles en encore est et étaient était été être eu eux fait fais fois font hors ici "
    "j je jusqu l la le les leur leurs lors lorsque lui ma mais me mes moi même mon ne ni non "
    "nos notre nous on ont ou où par parce pas pendant peu peut plus plusieurs pour pourquoi "
    "près puis qu que quand quel quelle quelles quels qui quoi sa se selon ses si sien son sont "
    "sous sur ta te tes toi ton tous tout toute toutes très tu un une vos votre vous y"
)

#: English stop words: the same job for the logs and the english messages.
_ENGLISH_STOP_WORDS = (
    "a about above after again against all also am an and any are as at be because been before "
    "being below between both but by can could did do does doing done down during each few for "
    "from further had has have having he her here hers herself him himself his how i if in into "
    "is it its itself just me might more most much must my myself no nor not now of off on once "
    "only or other our ours ourselves out over own same she should so some such than that the "
    "their theirs them themselves then there these they this those through to too under until up "
    "use used using very was we were what when where which while who whom why will with would "
    "you your yours yourself yourselves"
)

#: French stop words: the words that carry no operational meaning.
FRENCH_STOPWORDS: frozenset[str] = frozenset(_FRENCH_STOP_WORDS.split())

#: English stop words: the same job for the logs and the english messages.
ENGLISH_STOPWORDS: frozenset[str] = frozenset(_ENGLISH_STOP_WORDS.split())

#: Every stop word of the index, French and English.
STOPWORDS: frozenset[str] = FRENCH_STOPWORDS | ENGLISH_STOPWORDS


def fold(text: str) -> str:
    """Lowercase a text and drop its accents, so ``Connexions`` matches ``connexions``."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return stripped.lower()


def raw_tokens(text: str) -> list[str]:
    """The folded words of a text, stop words included."""
    return _TOKEN.findall(fold(text))


def tokenize(text: str) -> list[str]:
    """The meaningful words of a text: no punctuation, no stop word, no lone letter."""
    return [token for token in raw_tokens(text) if len(token) > 1 and token not in STOPWORDS]


def _norm(weights: Iterable[float]) -> float:
    """The euclidean norm of a collection of weights."""
    return math.sqrt(sum(weight * weight for weight in weights))


class SemanticIndex:
    """A tf-idf index over short documents, with a cosine search."""

    def __init__(self) -> None:
        self._documents: dict[str, Counter[str]] = {}
        self._frequencies: Counter[str] = Counter()

    def __len__(self) -> int:
        return len(self._documents)

    def index(self, document_id: str, text: str) -> None:
        """Add a document, or replace the one that carries the same id."""
        self.remove(document_id)
        counts = Counter(tokenize(text))
        self._documents[document_id] = counts
        for token in counts:
            self._frequencies[token] += 1

    def remove(self, document_id: str) -> None:
        """Forget a document; the document frequencies follow it."""
        counts = self._documents.pop(document_id, None)
        if counts is None:
            return
        for token in counts:
            self._frequencies[token] -= 1
            if self._frequencies[token] <= 0:
                del self._frequencies[token]

    def search(
        self, query: str, top_k: int = 5, within: Iterable[str] | None = None
    ) -> tuple[tuple[str, float], ...]:
        """The documents closest to the query, best first, ties broken by id.

        ``within`` restricts the search to a set of document ids - which is how
        one namespace is searched while the frequencies stay those of the whole
        corpus, so the same record always gets the same score.
        """
        wanted = Counter(tokenize(query))
        if not wanted or not self._documents:
            return ()
        weights = {token: count * self._idf(token) for token, count in wanted.items()}
        query_norm = _norm(weights.values())
        if query_norm <= 0:
            return ()
        candidates = self._documents if within is None else within
        scored = [
            (document_id, self._cosine(document_id, weights, query_norm))
            for document_id in candidates
            if document_id in self._documents
        ]
        kept = [(document_id, score) for document_id, score in scored if score > 0.0]
        kept.sort(key=lambda pair: (-pair[1], pair[0]))
        return tuple(kept[:top_k])

    def similarity(self, left: str, right: str) -> float:
        """The cosine between two documents of the index."""
        if left not in self._documents or right not in self._documents:
            return 0.0
        first, second = self._documents[left], self._documents[right]
        shared = set(first) & set(second)
        if not shared:
            return 0.0
        dot = sum(first[token] * second[token] * self._idf(token) ** 2 for token in shared)
        norm = self._document_norm(left) * self._document_norm(right)
        return dot / norm if norm else 0.0

    def _idf(self, token: str) -> float:
        """The inverse document frequency of a token, smoothed."""
        total = len(self._documents)
        return math.log((total + 1) / (self._frequencies.get(token, 0) + 1)) + 1.0

    def _cosine(self, document_id: str, weights: dict[str, float], query_norm: float) -> float:
        """The cosine between the query and one document."""
        counts = self._documents[document_id]
        dot = 0.0
        for token, weight in weights.items():
            count = counts.get(token, 0)
            if count:
                dot += weight * count * self._idf(token)
        if dot <= 0.0:
            return 0.0
        return dot / (query_norm * self._document_norm(document_id))

    def _document_norm(self, document_id: str) -> float:
        """The norm of the tf-idf vector of a document."""
        counts = self._documents[document_id]
        total = 0.0
        for token, count in counts.items():
            weight = count * self._idf(token)
            total += weight * weight
        return math.sqrt(total)


__all__ = [
    "ENGLISH_STOPWORDS",
    "FRENCH_STOPWORDS",
    "STOPWORDS",
    "SemanticIndex",
    "fold",
    "raw_tokens",
    "tokenize",
]
