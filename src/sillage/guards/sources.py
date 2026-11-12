"""Sources of a fiche: no statement without an episode (EF-VAL-02).

Chapter 10.4 states it as a rule of layer 2 - "une fiche sans source n'est pas
publiable" - and chapter 16 sets the target: **100 % of the statements sourced**.
The control is done here, by code, on the fiche as the writer agent produced it:
a model may forget a source, may invent one, or may add a statement that no
episode backs (R6). Code does not.

Three functions, one invariant:

* :func:`unsourced_claims` - the statements that cite no episode;
* :func:`check_claims` - the veto, raised before any publication;
* :func:`coverage` - the share of sourced statements, computed, never estimated
  (ENF-IA-01).

The identifiers of the episodes are checked for **presence**, not for existence:
the catalogue is the one that knows whether ``ep-1a2b3c`` is real, and the
guards never reach for a backend. A claim citing an episode of another perimeter
is stopped by layer 1 (the policy) and by the scope of the fiche, not here.
"""

from __future__ import annotations

from sillage.kernel.errors import UnsourcedClaim
from sillage.kernel.types import Claim, Fiche

#: A fiche with no statement at all is vacuously complete: nothing is unsourced.
FULL_COVERAGE = 1.0


def unsourced_claims(fiche: Fiche) -> tuple[Claim, ...]:
    """The statements of ``fiche`` that cite no episode, in their order."""
    return tuple(claim for claim in fiche.claims if not claim.sourced)


def check_claims(fiche: Fiche) -> None:
    """Refuse a fiche holding at least one unsourced statement (EF-VAL-02).

    The refusal names every offending statement: the referent sees what to fix,
    and the journal keeps the proof that the control ran.
    """
    missing = unsourced_claims(fiche)
    if not missing:
        return
    raise UnsourcedClaim(
        f"{len(missing)} affirmation(s) sans épisode source : la fiche n'est pas publiable",
        count=len(missing),
        claims=tuple(claim.text for claim in missing),
    )


def coverage(fiche: Fiche) -> float:
    """Share of the statements that cite an episode: 1.0 is what a publication needs."""
    if not fiche.claims:
        return FULL_COVERAGE
    sourced = sum(1 for claim in fiche.claims if claim.sourced)
    return sourced / len(fiche.claims)


def is_publishable(fiche: Fiche) -> bool:
    """The code answer to "may this fiche be published?": every statement sourced."""
    return not unsourced_claims(fiche)


__all__ = ["FULL_COVERAGE", "check_claims", "coverage", "is_publishable", "unsourced_claims"]
