from datetime import UTC, datetime, timedelta

import pytest

from lcftp.brokers.base import BrokerUnavailableError
from lcftp.brokers.testing import (
    FakeBroker,
    assert_broker_contract,
    make_candidate,
)

NOW = datetime(2026, 11, 10, 3, 0, tzinfo=UTC)


def _candidates() -> list:
    return [
        make_candidate(1, mag=15.0, when=NOW - timedelta(hours=1)),
        make_candidate(2, mag=19.0, when=NOW - timedelta(hours=1)),
        make_candidate(3, mag=15.0, when=NOW - timedelta(days=2)),
    ]


async def test_fake_broker_contract() -> None:
    broker = FakeBroker(_candidates())
    found = await assert_broker_contract(
        broker, since=NOW - timedelta(days=1), mag_limit=18.0
    )
    assert [c.dia_object_id for c in found] == [1]


async def test_fake_broker_failures() -> None:
    broker = FakeBroker(_candidates(), fail_times=1)
    with pytest.raises(BrokerUnavailableError):
        [c async for c in broker.poll(NOW, 20.0)]
    assert [c async for c in broker.poll(NOW - timedelta(days=3), 20.0)]


async def test_fake_broker_records_polls() -> None:
    broker = FakeBroker(_candidates())
    [c async for c in broker.poll(NOW, 17.5)]
    assert broker.polls == [(NOW, 17.5)]
