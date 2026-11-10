from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import respx
from pydantic import HttpUrl, SecretStr
from support_api import BASE, FakeApi

from lcftp_client.clock import FakeClock
from lcftp_client.config import ClientConfig
from lcftp_client.session import LcftpClient
from lcftp_models.observatory import ObservatoryProfile
from lcftp_models.plan import ObservationPlan

T0 = datetime(2026, 11, 10, 3, tzinfo=UTC)


async def _no_sleep(delay: float) -> None:
    return None


@pytest.fixture
def api(
    profile: ObservatoryProfile,
    plan_factory: Callable[..., ObservationPlan],
) -> Iterator[FakeApi]:
    with respx.mock(assert_all_called=False) as router:
        yield FakeApi(
            router, profile, lambda w: plan_factory(start=w.start, plan_id="p")
        )


@pytest.fixture
def config(tmp_path: Path) -> ClientConfig:
    return ClientConfig(
        server_url=HttpUrl(BASE),
        token=SecretStr("lcftp_tok"),
        observatory_id="obs1",
        spool_dir=tmp_path / "spool",
        driver_config={"image_size": 32},
    )


async def test_night_once(api: FakeApi, config: ClientConfig) -> None:
    async with LcftpClient(
        config, clock=FakeClock(T0), sleep=_no_sleep
    ) as client:
        summary = await client.night_once(T0, T0 + timedelta(hours=8))
        status = client.status()
    assert summary.uploaded == 3
    assert len(api.stored) == 3
    assert len(api.outcomes) == 3
    assert status.plan_id == "p"
    assert status.outcomes == {"observed": 3}
    assert status.pending_frames == 0


async def test_offline_uses_stored_plan(
    api: FakeApi, config: ClientConfig
) -> None:
    async with LcftpClient(
        config, clock=FakeClock(T0), sleep=_no_sleep
    ) as client:
        plan = await client.request_plan(T0, T0 + timedelta(hours=8))
    api.down = True
    async with LcftpClient(
        config, clock=FakeClock(T0), sleep=_no_sleep
    ) as client:
        summary = await client.night_once(T0, T0 + timedelta(hours=8))
        outcomes = client.status().outcomes
    assert summary.remaining == len(plan.items)
    assert outcomes == {"observed": len(plan.items)}
    assert client.status().pending_frames == len(plan.items)


async def test_offline_without_overlapping_plan(
    api: FakeApi, config: ClientConfig
) -> None:
    api.down = True
    async with LcftpClient(
        config, clock=FakeClock(T0), sleep=_no_sleep
    ) as client:
        with pytest.raises(RuntimeError, match="no stored plan"):
            await client.night_once(T0, T0 + timedelta(hours=8))


async def test_stop(api: FakeApi, config: ClientConfig) -> None:
    async with LcftpClient(
        config, clock=FakeClock(T0), sleep=_no_sleep
    ) as client:
        await client.request_plan(T0, T0 + timedelta(hours=8))
        client.stop()
        outcomes = await client.run()
    assert {o.reason.value for o in outcomes} == {"stopped"}
