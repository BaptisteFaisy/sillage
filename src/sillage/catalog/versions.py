"""Versioning of the fiches (EF-ENT-04).

A fiche is never edited in place: a correction produces a new version, and the
catalogue keeps the previous rows so a referent can see what changed and when.
Nothing here writes anything - ``bump`` returns the next version and the caller
decides when to store it, ``history`` only reads.
"""

from __future__ import annotations

from dataclasses import replace

from sillage.kernel.protocols import CatalogStore
from sillage.kernel.types import Fiche


def bump(fiche: Fiche, **changes: object) -> Fiche:
    """Return the next version of a fiche, with ``changes`` applied.

    The version number is managed here and cannot be forced by the caller: a
    ``version`` key in ``changes`` is ignored. The previous version is left
    untouched, which is what keeps the history readable.
    """
    changes.pop("version", None)
    return replace(fiche, version=fiche.version + 1, **changes)


def history(catalog: CatalogStore, fiche_id: str) -> tuple[Fiche, ...]:
    """Every version of a fiche the catalogue kept, oldest first.

    The catalogue is asked version by version, up to the current one: a store
    that keeps a single row simply answers with that row, and a store that
    ignores the version argument cannot produce a duplicate, since the versions
    are keyed by their own number.
    """
    current = catalog.get_fiche(fiche_id)
    if current is None:
        return ()
    versions: dict[int, Fiche] = {}
    for number in range(1, current.version + 1):
        found = _version(catalog, fiche_id, number)
        if found is not None:
            versions[found.version] = found
    versions.setdefault(current.version, current)
    return tuple(versions[number] for number in sorted(versions))


def _version(catalog: CatalogStore, fiche_id: str, number: int) -> Fiche | None:
    """One stored version, ``None`` when the catalogue does not keep it."""
    try:
        return catalog.get_fiche(fiche_id, number)
    except TypeError:
        return None
