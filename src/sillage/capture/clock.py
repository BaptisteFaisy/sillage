"""Clocks of the capture: one source of truth for "now".

``kernel.protocols.Clock`` is the port; the kernel deliberately ships no
implementation, because a module that reads the machine clock on its own makes
every test irreproducible. The capture needs two: the real one for the deployed
stack and the default service, and a frozen one for the tests, the demo and the
replay bench, so a freshness figure never moves between two runs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

#: Instant a frozen clock starts on: the day of the demo scenario.
DEMO_MOMENT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


class SystemClock:
    """The machine clock, in UTC."""

    def now(self) -> datetime:
        """The current instant."""
        return datetime.now(UTC)


class FrozenClock:
    """A clock that only moves when it is asked to."""

    def __init__(self, moment: datetime | None = None) -> None:
        self._moment = aware(moment or DEMO_MOMENT)

    def now(self) -> datetime:
        """The frozen instant."""
        return self._moment

    def advance(self, *, seconds: int = 0, days: int = 0) -> datetime:
        """Move the clock forward and return the new instant."""
        self._moment = self._moment + timedelta(seconds=seconds, days=days)
        return self._moment


def aware(moment: datetime) -> datetime:
    """Make an instant timezone aware: a naive date is read as UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def parse_moment(value: Any) -> datetime | None:
    """Read an instant: a ``datetime``, an ISO string (``Z`` accepted), or nothing.

    Every source dates its payload its own way - an ISO string with a ``Z``, a
    naive local date, an epoch - and the capture needs one rule for all of them.
    An unreadable date reads as "unknown", never as "now": a trace must not be
    silently moved to the present (ENF-IA-01).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return aware(value)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return aware(datetime.fromisoformat(text))
    except ValueError:
        return None


__all__ = ["DEMO_MOMENT", "FrozenClock", "SystemClock", "aware", "parse_moment"]
