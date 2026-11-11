"""Versions: every correction of a fiche keeps the previous row (EF-ENT-04).

``catalog/local.py`` does not exist yet, so the history is read through a local
fake of the catalogue that keeps one row per version, with the exact signature
of ``kernel.protocols.CatalogStore.get_fiche``. Two degenerate stores are faked
too: one that keeps a single row and one that ignores the version argument,
because ``history`` must stay honest with both.
"""

from __future__ import annotations

from dataclasses import replace

from sillage.catalog.versions import bump, history
from sillage.kernel.types import Fiche, Level, Scope, ScopeKind


class VersionedCatalog:
    """Local fake keeping one row per version of a fiche."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, int], Fiche] = {}

    def put_fiche(self, fiche: Fiche) -> None:
        self._rows[(fiche.fiche_id, fiche.version)] = fiche

    def get_fiche(self, fiche_id: str, version: int | None = None) -> Fiche | None:
        if version is not None:
            return self._rows.get((fiche_id, version))
        kept = [fiche for key, fiche in self._rows.items() if key[0] == fiche_id]
        return max(kept, key=lambda fiche: fiche.version) if kept else None


class LatestOnlyCatalog(VersionedCatalog):
    """A store that keeps the last version and forgets the previous ones."""

    def put_fiche(self, fiche: Fiche) -> None:
        self._rows = {key: value for key, value in self._rows.items() if key[0] != fiche.fiche_id}
        super().put_fiche(fiche)


class FlatCatalog(LatestOnlyCatalog):
    """A store that answers with the latest version whatever is asked."""

    def get_fiche(self, fiche_id: str, version: int | None = None) -> Fiche | None:
        return super().get_fiche(fiche_id, None)


def make_fiche(**changes: object) -> Fiche:
    base = Fiche(
        fiche_id="f-connexions",
        scope=Scope(kind=ScopeKind.CLIENT, client_id="a"),
        title="Connexions a la base epuisees",
        body="Le deploiement de 13h50 a porte le pool de 10 a 50 connexions par instance.",
        level=Level.VALIDATED,
    )
    return replace(base, **changes)


class TestBump:
    def test_the_version_goes_up(self) -> None:
        assert bump(make_fiche()).version == 2

    def test_the_changes_are_applied(self) -> None:
        updated = bump(make_fiche(), body="Nouveau corps", level=Level.PROVEN)
        assert updated.body == "Nouveau corps"
        assert updated.level is Level.PROVEN
        assert updated.version == 2

    def test_the_previous_version_is_untouched(self) -> None:
        fiche = make_fiche()
        bump(fiche, title="Autre titre")
        assert fiche.title == "Connexions a la base epuisees"
        assert fiche.version == 1

    def test_the_version_cannot_be_forced(self) -> None:
        assert bump(make_fiche(), version=99).version == 2

    def test_every_bump_adds_one(self) -> None:
        fiche = make_fiche()
        for _ in range(3):
            fiche = bump(fiche)
        assert fiche.version == 4


class TestHistory:
    def test_the_versions_come_back_oldest_first(self) -> None:
        catalog = VersionedCatalog()
        fiche = make_fiche()
        for index in range(3):
            catalog.put_fiche(fiche)
            fiche = bump(fiche, title=f"Correction {index + 1}")
        versions = history(catalog, "f-connexions")
        assert [found.version for found in versions] == [1, 2, 3]
        assert versions[0].title == "Connexions a la base epuisees"
        assert versions[-1].title == "Correction 2"

    def test_an_unknown_fiche_has_no_history(self) -> None:
        assert history(VersionedCatalog(), "f-inconnue") == ()

    def test_a_store_keeping_only_the_latest_answers_once(self) -> None:
        catalog = LatestOnlyCatalog()
        fiche = make_fiche()
        for index in range(3):
            catalog.put_fiche(fiche)
            fiche = bump(fiche, title=f"Correction {index + 1}")
        versions = history(catalog, "f-connexions")
        assert [fiche.version for fiche in versions] == [3]

    def test_a_store_ignoring_the_version_does_not_duplicate(self) -> None:
        catalog = FlatCatalog()
        fiche = make_fiche()
        for _ in range(3):
            catalog.put_fiche(fiche)
            fiche = bump(fiche)
        assert [found.version for found in history(catalog, "f-connexions")] == [3]

    def test_another_fiche_does_not_leak_into_the_history(self) -> None:
        catalog = VersionedCatalog()
        catalog.put_fiche(make_fiche())
        catalog.put_fiche(bump(make_fiche(fiche_id="f-autre")))
        assert [fiche.version for fiche in history(catalog, "f-connexions")] == [1]
