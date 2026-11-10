"""Clocks, so plan execution can be tested without waiting."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Protocol

__all__ = ["Clock", "FakeClock", "SystemClock"]


class Clock(Protocol):
    """Source of the current time and of waiting."""

    def now(self) -> datetime:
        """Return the current UTC time."""

    async def sleep_until(self, when: datetime) -> None:
        """Wait until ``when`` (return at once if it has passed)."""


class SystemClock:
    """The real clock."""

    def now(self) -> datetime:
        """Return the current UTC time."""
        return datetime.now(tz=UTC)

    async def sleep_until(self, when: datetime) -> None:
        """Sleep until ``when``."""
        delay = (when - self.now()).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)


class FakeClock:
    """A clock that only moves when told to; sleeping is instant."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        """Return the fake current time."""
        return self._now

    async def sleep_until(self, when: datetime) -> None:
        """Jump forward to ``when``."""
        self._now = max(self._now, when)
        await asyncio.sleep(0)

    def advance(self, delta: timedelta) -> None:
        """Move the clock forward."""
        self._now += delta
