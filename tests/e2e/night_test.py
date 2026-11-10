from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import HttpUrl, SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from lcftp.brokers.testing import FakeBroker, make_candidate
from lcftp.db.schema import FrameRow, OutcomeRow, PlanRow
from lcftp.db.session import create_sessionmaker
from lcftp.ingest.worker import run_ingest_once
from lcftp_client.clock import FakeClock
from lcftp_client.config import ClientConfig
from lcftp_client.session import LcftpClient
from lcftp_models.plan import ObservationPlan
from tests.e2e.conftest import Stack
from tests.support.factories import make_device, make_observatory, make_user

START = datetime(2026, 11, 10, 2, tzinfo=UTC)
END = START + timedelta(hours=10)


async def test_simulated_night(
    stack: Stack, engine: AsyncEngine, db_session: AsyncSession, tmp_path: Path
) -> None:
    user = await make_user(db_session)
    await make_observatory(db_session, user, "obs1", limiting_mag=15)
    headers = await make_device(db_session, "obs1")
    await db_session.commit()
    token = headers["Authorization"].removeprefix("Bearer ")

    candidates = [
        make_candidate(
            1000 + i,
            ra=(20 + 12 * i) % 360,
            dec=10 + (i % 3) * 10,
            mag=11 + (i % 4),
            when=START - timedelta(hours=2),
        )
        for i in range(10)
    ]
    report = await run_ingest_once(
        [FakeBroker(candidates)],
        create_sessionmaker(engine),
        stack.settings,
        START,
    )
    assert report.per_broker == {"fake": 10}

    config = ClientConfig(
        server_url=HttpUrl(stack.url),
        token=SecretStr(token),
        observatory_id="obs1",
        spool_dir=tmp_path / "spool",
        driver_config={"seed": 1, "cloud_rate": 0.2, "image_size": 64},
    )

    async def no_sleep(delay: float) -> None:
        return None

    async with LcftpClient(
        config, clock=FakeClock(START), sleep=no_sleep
    ) as client:
        summary = await client.night_once(START, END)
        plan = client.spool.current_plan()
    assert isinstance(plan, ObservationPlan)
    assert plan.items
    assert summary.remaining == 0
    assert summary.bad == 0

    db_session.expire_all()
    frames = (await db_session.execute(select(FrameRow))).scalars().all()
    outcomes = (await db_session.execute(select(OutcomeRow))).scalars().all()
    plans = await db_session.scalar(select(func.count()).select_from(PlanRow))
    assert plans == 1
    assert sorted(o.item_id for o in outcomes) == [
        i.item_id for i in plan.items
    ]
    observed = [o for o in outcomes if o.status == "observed"]
    assert observed
    assert len([f for f in frames if f.status == "received"]) == len(observed)

    s3: Any = stack.s3
    listing = s3.list_objects_v2(Bucket=stack.bucket, Prefix="lcftp/alerts/")
    keys = {obj["Key"] for obj in listing.get("Contents", [])}
    for frame in frames:
        expected = (
            f"lcftp/alerts/{frame.dia_object_id}/obs1/{plan.plan_id}/"
            f"{frame.frame_id}.fits"
        )
        assert expected in keys
        head = s3.head_object(Bucket=stack.bucket, Key=expected)
        assert head["Metadata"]["sha256"] == frame.sha256
    assert list((tmp_path / "spool" / "frames").iterdir()) == []
