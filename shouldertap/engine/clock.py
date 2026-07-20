"""Injectable clock (build-plan §"Shared infra"): threaded through router/asker/scheduler so
dedup windows, quiet hours, and escalation/give-up timing are deterministically testable.

Datetimes throughout the engine are naive UTC (never tz-aware) -- SQLite round-trips naive
datetimes cleanly, and a single convention avoids aware/naive comparison bugs between the
Pydantic contracts, the ORM, and APScheduler.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return utcnow()


class ManualClock:
    """Test double: advances only when told to."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start if start is not None else utcnow()

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def set(self, when: datetime) -> None:
        self._now = when
