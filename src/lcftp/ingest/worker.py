"""Poll brokers into the candidate cache."""

import asyncio
import logging
import os
import signal
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from ..brokers.base import BrokerSource, BrokerUnavailableError
from ..brokers.loader import load_brokers
from ..config import Settings
from ..db.session import create_engine, create_sessionmaker
from ..stores.candidates import BrokerState, SqlCandidateStore
from ..stores.observatories import SqlObservatoryStore

__all__ = ["MAX_BACKOFF", "IngestReport", "run_ingest_once", "run_worker"]

MAX_BACKOFF = timedelta(hours=1)

logger = logging.getLogger(__name__)


class IngestReport(BaseModel):
    """What one ingest pass did."""

    per_broker: dict[str, int] = Field(default_factory=dict)
    """Number of candidates ingested from each broker polled successfully."""

    skipped: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    expired: int = 0


async def _poll_one(
    broker: BrokerSource,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    mag_limit: float,
    now: datetime,
    report: IngestReport,
) -> None:
    async with sessions() as session, session.begin():
        store = SqlCandidateStore(session)
        state = await store.get_state(broker.name)
        if state.next_attempt and now < state.next_attempt:
            report.skipped.append(broker.name)
            return
        if state.high_water_mark:
            since = state.high_water_mark - settings.ingest_lookback
        else:
            since = now - settings.candidate_max_age
        count = 0
        newest = state.high_water_mark
        try:
            async with session.begin_nested():
                async for candidate in broker.poll(since, mag_limit):
                    await store.upsert(candidate)
                    count += 1
                    if newest is None or candidate.latest_alert_time > newest:
                        newest = candidate.latest_alert_time
        except Exception as e:
            if not isinstance(e, BrokerUnavailableError):
                logger.exception("Unexpected error polling %s", broker.name)
            failures = state.consecutive_failures + 1
            delay = min(settings.ingest_interval * 2**failures, MAX_BACKOFF)
            logger.warning(
                "Broker %s unavailable (%d in a row): %s",
                broker.name,
                failures,
                e,
            )
            await store.save_state(
                state.model_copy(
                    update={
                        "consecutive_failures": failures,
                        "next_attempt": now + delay,
                    }
                )
            )
            report.failed.append(broker.name)
            return
        await store.save_state(
            BrokerState(
                broker=broker.name,
                high_water_mark=newest,
                last_success=now,
                consecutive_failures=0,
                next_attempt=None,
            )
        )
        report.per_broker[broker.name] = count


async def run_ingest_once(
    brokers: Sequence[BrokerSource],
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    now: datetime,
) -> IngestReport:
    """Poll every broker once and expire stale candidates."""
    report = IngestReport()
    async with sessions() as session:
        faintest = await SqlObservatoryStore(session).faintest_limiting_mag()
    if faintest is None:
        report.skipped = [b.name for b in brokers]
    else:
        mag_limit = faintest + settings.ingest_mag_margin
        for broker in brokers:
            await _poll_one(broker, sessions, settings, mag_limit, now, report)
    async with sessions() as session, session.begin():
        store = SqlCandidateStore(session)
        report.expired = await store.expire(now - settings.candidate_max_age)
    return report


async def run_worker(
    settings: Settings,
    *,
    brokers: Sequence[BrokerSource] | None = None,
    engine: AsyncEngine | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """Run ingest every ``ingest_interval`` until stopped.

    SIGTERM and SIGINT stop the loop after the current pass.
    """
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    db = engine or create_engine(settings)
    sessions = create_sessionmaker(db)
    sources = brokers or load_brokers(settings.brokers, os.environ)
    try:
        while True:
            report = await run_ingest_once(
                sources, sessions, settings, datetime.now(tz=UTC)
            )
            logger.info("Ingest pass: %s", report.model_dump_json())
            try:
                await asyncio.wait_for(
                    stop.wait(), settings.ingest_interval.total_seconds()
                )
            except TimeoutError:
                continue
            break
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)
        if engine is None:
            await db.dispose()
