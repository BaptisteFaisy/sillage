"""Offline ports of the catalogue: blobs, journal, notifications.

Three small backends used by the tests, the demo, the replay bench and the
offline portal. Each implements exactly one kernel protocol and is deliberately
narrower than the AWS backend it stands in for: an offline port must be readable,
not clever.

The journal is **append-only** on purpose (ENF-SEC-05): the class has no method
that modifies or removes an entry, so "nobody edited the log" is a property of the
type, not a promise in a comment.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from sillage.kernel.types import Decision, JournalEntry


@dataclass(frozen=True)
class Notification:
    """One message a referent received, as the offline notifier recorded it."""

    referent: str
    subject: str
    body: str
    fiche_id: str = ""


class LocalBlobStore:
    """``BlobStore`` in a dictionary, or under a directory when a root is given.

    A key keeps every payload it received, so ``delete`` can answer how many
    versions disappeared - the same question the deployed stack asks a versioned
    S3 bucket.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root
        self._versions: dict[str, list[str]] = {}

    def put(self, key: str, payload: str) -> str:
        """Store a payload under a key and return the key."""
        self._versions.setdefault(key, []).append(payload)
        if self.root is not None:
            self._path(key).write_text(payload, encoding="utf-8")
        return key

    def get(self, key: str) -> str | None:
        """The latest payload of a key, ``None`` when the key is unknown."""
        if self.root is not None:
            path = self._path(key)
            return path.read_text(encoding="utf-8") if path.exists() else None
        versions = self._versions.get(key)
        return versions[-1] if versions else None

    def delete(self, key: str) -> int:
        """Remove a key and every version of it; return how many disappeared."""
        removed = len(self._versions.pop(key, []))
        if self.root is not None:
            path = self._path(key)
            if path.exists():
                path.unlink()
                removed = max(removed, 1)
        return removed

    def keys(self) -> tuple[str, ...]:
        """Every known key, sorted: used by the tests and by an export."""
        return tuple(sorted(self._versions))

    def versions_of(self, key: str) -> int:
        """How many versions a key kept, zero when unknown."""
        return len(self._versions.get(key, []))

    def _path(self, key: str) -> Path:
        """File holding one key: the key is quoted, so it stays one file name."""
        assert self.root is not None  # guarded by the callers
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root / f"{quote(key, safe='')}.json"


class ListJournal:
    """``Journal`` on a list, append-only, newest first on read.

    ``query`` filters by tool and by decision, which is what the portal's journal
    screen and the policy tests ask for (EF-ADM-02). There is no ``update`` and
    no ``delete``: a call or a refusal, once written, stays written.
    """

    def __init__(self, entries: Sequence[JournalEntry] = ()) -> None:
        self._entries: tuple[JournalEntry, ...] = tuple(entries)

    def append(self, entry: JournalEntry) -> None:
        """Add one entry; nothing can replace it afterwards."""
        self._entries = (*self._entries, entry)

    def query(
        self, *, limit: int = 50, tool: str = "", decision: Decision | None = None
    ) -> tuple[JournalEntry, ...]:
        """Entries matching the filters, the newest first."""
        kept = [
            entry
            for entry in self._entries
            if (not tool or entry.tool == tool) and (decision is None or entry.decision is decision)
        ]
        ordered = sorted(kept, key=lambda entry: (entry.at, entry.entry_id), reverse=True)
        return tuple(ordered[:limit]) if limit > 0 else tuple(ordered)

    def entries(self) -> tuple[JournalEntry, ...]:
        """Every entry, oldest first: an export, never an edition."""
        return self._entries

    def __len__(self) -> int:
        """Number of entries written so far."""
        return len(self._entries)


class ListNotifier:
    """``Notifier`` on a list: what a referent would have received."""

    def __init__(self) -> None:
        self._sent: tuple[Notification, ...] = ()

    def notify(self, *, referent: str, subject: str, body: str, fiche_id: str = "") -> None:
        """Record one message for a referent."""
        self._sent = (*self._sent, Notification(referent, subject, body, fiche_id))

    def messages(self) -> tuple[Notification, ...]:
        """Every message sent, oldest first."""
        return self._sent

    def for_referent(self, referent: str) -> tuple[Notification, ...]:
        """Messages one referent received, oldest first."""
        return tuple(message for message in self._sent if message.referent == referent)

    def __len__(self) -> int:
        """Number of messages sent so far."""
        return len(self._sent)


__all__ = ["ListJournal", "ListNotifier", "LocalBlobStore", "Notification"]
