"""The offline catalogue: one object, four kernel ports, zero dependency.

``LocalCatalog`` is the store every test, the demo, the replay bench and the
offline portal use. It satisfies ``CatalogStore`` (the entities of chapter 14),
``BlobStore`` (the raw trace copy), ``Journal`` (append-only, EF-ADM-02) and
``Notifier`` (a referent decision to ask), in memory, deterministically.

It is split so no file and no class grows:

* :mod:`sillage.catalog.local_state` - the dictionaries and the ordering rules;
* :mod:`sillage.catalog.local_rows` - one mixin per family of chapter 14;
* :mod:`sillage.catalog.local_cascade` - ``delete_scope`` (EF-ENT-03, 17.2);
* :mod:`sillage.catalog.local_ports` - blobs, journal, notifications.

Nothing here imports boto3, calls a model or reads the machine clock unless no
clock was injected: a freshness test stays reproducible.
"""

from __future__ import annotations

from collections.abc import Sequence

from sillage.catalog.local_cascade import CascadeRows
from sillage.catalog.local_ports import ListJournal, ListNotifier, LocalBlobStore, Notification
from sillage.catalog.local_rows import (
    ClientRows,
    EpisodeRows,
    FicheRows,
    LessonRows,
    TraceRows,
    UsageRows,
)
from sillage.catalog.local_state import CatalogState
from sillage.kernel.protocols import Clock
from sillage.kernel.types import Decision, JournalEntry


class LocalCatalog(
    CatalogState,
    ClientRows,
    TraceRows,
    EpisodeRows,
    LessonRows,
    FicheRows,
    UsageRows,
    CascadeRows,
):
    """In-memory catalogue: one object implementing the four catalogue ports.

    The ports are composed rather than reimplemented: ``blobs``, ``journal`` and
    ``notifier`` are the very objects the grouped methods below delegate to, so a
    test can count notifications or read the journal without going through the
    catalogue, while the demo stack still receives one object per port.
    """

    def __init__(
        self,
        clock: Clock | None = None,
        *,
        blobs: LocalBlobStore | None = None,
        journal: ListJournal | None = None,
        notifier: ListNotifier | None = None,
    ) -> None:
        super().__init__(clock)
        self._blobs = blobs if blobs is not None else LocalBlobStore()
        self._journal = journal if journal is not None else ListJournal()
        self._notifier = notifier if notifier is not None else ListNotifier()

    # -- BlobStore --------------------------------------------------------- #
    def put(self, key: str, payload: str) -> str:
        """Store a payload under a key (the raw trace of a job) and return the key."""
        return self._blobs.put(key, payload)

    def get(self, key: str) -> str | None:
        """The latest payload stored under a key, ``None`` when unknown."""
        return self._blobs.get(key)

    def delete(self, key: str) -> int:
        """Remove a key and every version of it; return how many disappeared."""
        return self._blobs.delete(key)

    # -- Journal ----------------------------------------------------------- #
    def append(self, entry: JournalEntry) -> None:
        """Append one journal entry; nothing can rewrite it afterwards."""
        self._journal.append(entry)

    def query(
        self, *, limit: int = 50, tool: str = "", decision: Decision | None = None
    ) -> Sequence[JournalEntry]:
        """Read the journal, newest first, filtered by tool and by decision."""
        return self._journal.query(limit=limit, tool=tool, decision=decision)

    # -- Notifier ---------------------------------------------------------- #
    def notify(self, *, referent: str, subject: str, body: str, fiche_id: str = "") -> None:
        """Record a message for a referent (a fiche needs a decision)."""
        self._notifier.notify(referent=referent, subject=subject, body=body, fiche_id=fiche_id)

    # -- the composed ports ------------------------------------------------ #
    @property
    def blobs(self) -> LocalBlobStore:
        """The blob port behind this catalogue."""
        return self._blobs

    @property
    def journal(self) -> ListJournal:
        """The append-only journal behind this catalogue."""
        return self._journal

    @property
    def notifier(self) -> ListNotifier:
        """The notifier behind this catalogue."""
        return self._notifier

    def notifications(self) -> tuple[Notification, ...]:
        """Every notification sent through this catalogue, oldest first."""
        return self._notifier.messages()


__all__ = ["ListJournal", "ListNotifier", "LocalBlobStore", "LocalCatalog", "Notification"]
