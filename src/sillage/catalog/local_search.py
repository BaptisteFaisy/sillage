"""Lexical search over the fiches: deterministic, offline, no model.

``memory.search`` does the semantic work (an intention, AgentCore Memory); this
one answers the portal's search box, the offline demo and the tests, so it has to
be exact and reproducible.

Rules, all computed by code (ENF-IA-01):

* normalisation: lower case, accents folded, punctuation dropped - the folding is
  ``kernel.ids.slug``, so there is a single implementation in the project;
* French and English stop words are dropped: they say nothing about a fiche;
* the searched fields are the ones the specification asks for: title, body, steps
  and validity conditions, and never the author (ENF-RH-01);
* ranking: number of recognised terms, then trust level, then identifier. A
  creation date never ranks: it would favour the new over the true.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from sillage.kernel.ids import slug
from sillage.kernel.types import Fiche, Level

#: Trust order used as the second ranking key: proven first, withdrawn last.
LEVEL_RANK: dict[Level, int] = {
    Level.PROVEN: 5,
    Level.VALIDATED: 4,
    Level.PROPOSED: 3,
    Level.RAW: 2,
    Level.SET_ASIDE: 1,
    Level.WITHDRAWN: 0,
}

#: Words too common to say anything about a fiche, in both languages of the code.
"""Words of the two working languages, one per line, separated by spaces."""
_STOP_TEXT = """
au aux avec ce ces cet cette dans de des du elle en est et il ils je la le les leur lui
ma mais me mes moi mon ne nos notre nous on ou par pas pour qu que qui sa se ses son sur
ta te tes toi ton tu un une vos votre vous ca cela ceci dont donc alors apres avant comme
plus moins tres tout tous toute toutes autre autres etre avoir fait faire
the and for with from that this these those are was were been its not yes did does how what
when where which who why
"""

#: Words too common to say anything about a fiche, in both languages of the code.
STOP_WORDS: frozenset[str] = frozenset(word for word in _STOP_TEXT.split() if word)

#: Shortest word taken into account: one and two letter words carry no signal.
MIN_WORD = 3

#: Separators of a text: everything that is not a letter or a digit.
_SEPARATOR = re.compile(r"[^\w]+", re.UNICODE)


def words_of(text: str) -> tuple[str, ...]:
    """Folded words of a text: ``Connexions épuisées`` gives ``connexions epuisees``.

    Each word is folded by ``kernel.ids.slug`` - one folding implementation in the
    project - word by word, because ``slug`` truncates a long text and a search
    field is longer than a slug.
    """
    found: list[str] = []
    for token in _SEPARATOR.split(text or ""):
        if not token:
            continue
        word = slug(token)
        if len(word) >= MIN_WORD and word != "sans-titre":
            found.append(word)
    return tuple(found)


def query_words(terms: Iterable[str]) -> tuple[str, ...]:
    """Normalised query terms: folded, without stop words, without duplicates."""
    found: list[str] = []
    for term in terms:
        for word in words_of(str(term)):
            if word not in STOP_WORDS and word not in found:
                found.append(word)
    return tuple(found)


def searched_text(fiche: Fiche) -> str:
    """The fields a query is matched against: title, body, validity, steps."""
    return " ".join((fiche.title, fiche.body, fiche.validity, *fiche.steps))


def matches(word: str, fiche_words: Sequence[str]) -> bool:
    """True when a query word appears in the fiche, as a word or as a stem.

    The prefix match is what makes ``connexion`` find ``connexions`` and
    ``deploiement`` find ``deploiements``: French plurals are common enough that
    an exact match would miss half the catalogue.
    """
    return any(candidate == word or candidate.startswith(word) for candidate in fiche_words)


def score(fiche: Fiche, terms: Sequence[str]) -> int:
    """Number of distinct query terms the fiche contains."""
    fiche_words = words_of(searched_text(fiche))
    return sum(1 for term in terms if matches(term, fiche_words))


def rank(fiches: Iterable[Fiche], terms: Sequence[str], limit: int = 10) -> tuple[Fiche, ...]:
    """Fiches matching at least one query term, best first.

    The order is ``(-recognised terms, -level, fiche_id)``: with no term at all
    the answer is empty, never the whole catalogue.
    """
    scored = [(score(fiche, terms), fiche) for fiche in fiches]
    kept = [(found, fiche) for found, fiche in scored if found > 0]
    kept.sort(key=lambda pair: (-pair[0], -LEVEL_RANK.get(pair[1].level, 0), pair[1].fiche_id))
    ordered = tuple(fiche for _, fiche in kept)
    return ordered[:limit] if limit > 0 else ordered
