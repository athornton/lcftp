from datetime import UTC, datetime, timedelta

from lcftp_client.clock import FakeClock, SystemClock

T0 = datetime(2026, 11, 10, 3, tzinfo=UTC)


async def test_fake_clock_sleeps_instantly() -> None:
    clock = FakeClock(T0)
    await clock.sleep_until(T0 + timedelta(hours=1))
    assert clock.now() == T0 + timedelta(hours=1)
    await clock.sleep_until(T0)
    assert clock.now() == T0 + timedelta(hours=1)


def test_fake_clock_advance() -> None:
    clock = FakeClock(T0)
    clock.advance(timedelta(minutes=5))
    assert clock.now() == T0 + timedelta(minutes=5)


async def test_system_clock() -> None:
    clock = SystemClock()
    before = clock.now()
    assert before.tzinfo is UTC
    await clock.sleep_until(before - timedelta(seconds=1))
