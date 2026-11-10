from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from lcftp.brokers.testing import make_candidate
from lcftp.stores.candidates import BrokerState, SqlCandidateStore

NOW = datetime(2026, 11, 10, 3, 0, tzinfo=UTC)


async def test_merge_sources(db_session: AsyncSession) -> None:
    store = SqlCandidateStore(db_session)
    await store.upsert(make_candidate(1, broker="fake-a", when=NOW))
    await store.upsert(make_candidate(1, broker="fake-b", when=NOW))
    [found] = await store.query(30)
    assert {s.broker for s in found.sources} == {"fake-a", "fake-b"}


async def test_newer_alert_wins(db_session: AsyncSession) -> None:
    store = SqlCandidateStore(db_session)
    older = make_candidate(1, mag=16, when=NOW - timedelta(hours=2))
    newer = make_candidate(1, mag=14, when=NOW)
    await store.upsert(newer)
    await store.upsert(older)
    [found] = await store.query(30)
    assert found.mag == 14
    assert found.latest_alert_time == NOW
    assert found.first_seen == NOW - timedelta(hours=2)


async def test_query_filters_by_magnitude(db_session: AsyncSession) -> None:
    store = SqlCandidateStore(db_session)
    await store.upsert(make_candidate(1, mag=14, when=NOW))
    await store.upsert(make_candidate(2, mag=18, when=NOW))
    assert [c.dia_object_id for c in await store.query(15)] == [1]


async def test_expire(db_session: AsyncSession) -> None:
    store = SqlCandidateStore(db_session)
    await store.upsert(make_candidate(1, when=NOW - timedelta(days=8)))
    await store.upsert(make_candidate(2, when=NOW))
    assert await store.expire(NOW - timedelta(days=7)) == 1
    assert [c.dia_object_id for c in await store.query(30)] == [2]


async def test_broker_state(db_session: AsyncSession) -> None:
    store = SqlCandidateStore(db_session)
    assert await store.get_state("fake") == BrokerState(broker="fake")
    state = BrokerState(
        broker="fake", last_success=NOW, consecutive_failures=0
    )
    await store.save_state(state)
    assert await store.get_state("fake") == state
    assert await store.candidates_as_of(["fake", "never"]) == NOW
    assert await store.candidates_as_of(["never"]) is None
