import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import override

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from lcftp.brokers.testing import FakeBroker, make_candidate
from lcftp.config import Settings
from lcftp.db.session import create_sessionmaker
from lcftp.ingest.worker import run_ingest_once, run_worker
from lcftp.stores.candidates import SqlCandidateStore
from lcftp_models.candidate import Candidate
from tests.support.factories import make_observatory, make_user

NOW = datetime(2026, 11, 10, 3, 0, tzinfo=UTC)


async def _observatory(session: AsyncSession, mag: float = 19.0) -> None:
    user = await make_user(session)
    await make_observatory(session, user, limiting_mag=mag)
    await session.commit()


async def test_merges_brokers(
    engine: AsyncEngine, db_session: AsyncSession, settings: Settings
) -> None:
    await _observatory(db_session)
    brokers = [
        FakeBroker([make_candidate(1, when=NOW)], name="fake-a"),
        FakeBroker([make_candidate(1, when=NOW)], name="fake-b"),
    ]
    report = await run_ingest_once(
        brokers, create_sessionmaker(engine), settings, NOW
    )
    assert report.per_broker == {"fake-a": 1, "fake-b": 1}
    [found] = await SqlCandidateStore(db_session).query(30)
    assert {s.broker for s in found.sources} == {"fake-a", "fake-b"}


async def test_adaptive_magnitude_limit(
    engine: AsyncEngine, db_session: AsyncSession, settings: Settings
) -> None:
    await _observatory(db_session, 19.0)
    broker = FakeBroker([])
    await run_ingest_once([broker], create_sessionmaker(engine), settings, NOW)
    assert broker.polls == [(NOW - timedelta(days=7), 19.5)]


async def test_no_observatories_no_polling(
    engine: AsyncEngine, settings: Settings
) -> None:
    broker = FakeBroker([])
    report = await run_ingest_once(
        [broker], create_sessionmaker(engine), settings, NOW
    )
    assert broker.polls == []
    assert report.skipped == ["fake"]


async def test_backoff_and_recovery(
    engine: AsyncEngine, db_session: AsyncSession, settings: Settings
) -> None:
    await _observatory(db_session)
    sessions = create_sessionmaker(engine)
    flaky = FakeBroker(
        [make_candidate(1, when=NOW)], name="flaky", fail_times=2
    )
    good = FakeBroker([make_candidate(2, when=NOW)], name="good")

    report = await run_ingest_once([flaky, good], sessions, settings, NOW)
    assert report.failed == ["flaky"]
    assert report.per_broker == {"good": 1}
    store = SqlCandidateStore(db_session)
    state = await store.get_state("flaky")
    assert state.consecutive_failures == 1
    assert state.next_attempt == NOW + timedelta(minutes=10)

    report = await run_ingest_once([flaky], sessions, settings, NOW)
    assert report.skipped == ["flaky"]

    later = NOW + timedelta(minutes=10)
    await run_ingest_once([flaky], sessions, settings, later)
    db_session.expire_all()
    assert (await store.get_state("flaky")).consecutive_failures == 2
    assert (await store.get_state("flaky")).next_attempt == later + timedelta(
        minutes=20
    )

    latest = later + timedelta(minutes=20)
    report = await run_ingest_once([flaky], sessions, settings, latest)
    assert report.per_broker == {"flaky": 1}
    db_session.expire_all()
    state = await store.get_state("flaky")
    assert state.consecutive_failures == 0
    assert state.last_success == latest
    assert state.high_water_mark == NOW


async def test_high_water_mark_advances(
    engine: AsyncEngine, db_session: AsyncSession, settings: Settings
) -> None:
    await _observatory(db_session)
    sessions = create_sessionmaker(engine)
    broker = FakeBroker([make_candidate(1, when=NOW - timedelta(hours=1))])
    await run_ingest_once([broker], sessions, settings, NOW)
    await run_ingest_once([broker], sessions, settings, NOW)
    # The next poll overlaps the high-water mark to catch late detections.
    assert (
        broker.polls[1][0]
        == NOW - timedelta(hours=1) - settings.ingest_lookback
    )
    assert settings.ingest_lookback == timedelta(hours=1)


async def test_expires_old_candidates(
    engine: AsyncEngine, db_session: AsyncSession, settings: Settings
) -> None:
    await _observatory(db_session)
    store = SqlCandidateStore(db_session)
    await store.upsert(make_candidate(9, when=NOW - timedelta(days=8)))
    await db_session.commit()
    report = await run_ingest_once(
        [FakeBroker([])], create_sessionmaker(engine), settings, NOW
    )
    assert report.expired == 1


async def test_ready_reports_cache_age(
    engine: AsyncEngine,
    db_session: AsyncSession,
    settings: Settings,
    client: AsyncClient,
) -> None:
    await _observatory(db_session)
    alerce = FakeBroker([], name="alerce")
    await run_ingest_once([alerce], create_sessionmaker(engine), settings, NOW)
    r = await client.get("/health/ready")
    assert r.json()["candidates_as_of"] == "2026-11-10T03:00:00Z"


async def test_run_worker_stops(
    engine: AsyncEngine, settings: Settings
) -> None:
    broker = FakeBroker([])
    stop = asyncio.Event()
    stop.set()
    await asyncio.wait_for(
        run_worker(settings, brokers=[broker], engine=engine, stop=stop), 5
    )


class ExplodingBroker(FakeBroker):
    @override
    async def poll(
        self, since: datetime, mag_limit: float
    ) -> AsyncIterator[Candidate]:
        if since:
            raise RuntimeError("unexpected bug")
        yield make_candidate(1)  # pragma: no cover


async def test_unexpected_broker_error_is_contained(
    engine: AsyncEngine, db_session: AsyncSession, settings: Settings
) -> None:
    await _observatory(db_session)
    sessions = create_sessionmaker(engine)
    good = FakeBroker([make_candidate(2, when=NOW)], name="good")
    report = await run_ingest_once(
        [ExplodingBroker([], name="bad"), good], sessions, settings, NOW
    )
    assert report.failed == ["bad"]
    assert report.per_broker == {"good": 1}
    state = await SqlCandidateStore(db_session).get_state("bad")
    assert state.consecutive_failures == 1
