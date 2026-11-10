import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest
import respx
from click.testing import CliRunner
from support_api import BASE, FakeApi

from lcftp_client import cli
from lcftp_client.api import LcftpApiError
from lcftp_client.clock import FakeClock
from lcftp_client.config import ClientConfig, load_config
from lcftp_client.session import ClientStatus, LcftpClient
from lcftp_client.uploader import UploadSummary
from lcftp_models.errors import ErrorCode
from lcftp_models.observatory import ObservatoryProfile
from lcftp_models.plan import ObservationPlan

T0 = datetime(2026, 11, 10, 3, tzinfo=UTC)


@pytest.fixture
def api(
    profile: ObservatoryProfile,
    plan_factory: Callable[..., ObservationPlan],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[FakeApi]:
    async def no_sleep(delay: float) -> None:
        return None

    def factory(config: ClientConfig) -> LcftpClient:
        return LcftpClient(config, clock=FakeClock(T0), sleep=no_sleep)

    monkeypatch.setattr(cli, "make_client", factory)
    with respx.mock(assert_all_called=False) as router:
        yield FakeApi(
            router, profile, lambda w: plan_factory(start=w.start, plan_id="p")
        )


def _invoke(config: Path, *args: str) -> str:
    result = CliRunner().invoke(cli.main, ["--config", str(config), *args])
    assert result.exit_code == 0, result.output
    return result.output


def test_full_cycle(api: FakeApi, tmp_path: Path) -> None:
    config = tmp_path / "client.toml"
    spool = tmp_path / "spool"
    _invoke(
        config,
        "init",
        "--server",
        BASE,
        "--token",
        "lcftp_t",
        "--spool",
        str(spool),
    )
    saved = load_config(config)
    assert saved.observatory_id == "obs1"
    assert saved.driver == "simulated"
    assert saved.spool_dir == spool

    _invoke(
        config,
        "plan",
        "--start",
        "2026-11-10T03:00:00Z",
        "--end",
        "2026-11-10T11:00:00Z",
    )
    _invoke(config, "run")
    status = ClientStatus.model_validate_json(_invoke(config, "status"))
    assert status.outcomes == {"observed": 3}
    assert status.pending_frames == 3

    summary = json.loads(_invoke(config, "upload"))
    assert summary["uploaded"] == 3
    assert (
        ClientStatus.model_validate_json(
            _invoke(config, "status")
        ).pending_frames
        == 0
    )


def test_stop_file(api: FakeApi, tmp_path: Path) -> None:
    config = tmp_path / "client.toml"
    _invoke(
        config,
        "init",
        "--server",
        BASE,
        "--token",
        "t",
        "--spool",
        str(tmp_path / "s"),
    )
    _invoke(
        config,
        "plan",
        "--start",
        "2026-11-10T03:00:00Z",
        "--end",
        "2026-11-10T11:00:00Z",
    )
    _invoke(config, "stop")
    _invoke(config, "run")
    status = ClientStatus.model_validate_json(_invoke(config, "status"))
    assert status.outcomes == {"skipped": 3}


def test_next_window_before_start() -> None:
    now = datetime(2026, 11, 10, 0, tzinfo=UTC)  # 17:00 in Phoenix
    start, end = cli.next_window(now, time(19), time(5), "America/Phoenix")
    assert start == datetime(2026, 11, 10, 2, tzinfo=UTC)
    assert end == datetime(2026, 11, 10, 12, tzinfo=UTC)


def test_next_window_during_night() -> None:
    now = datetime(2026, 11, 10, 6, tzinfo=UTC)  # 23:00 in Phoenix
    start, end = cli.next_window(now, time(19), time(5), "America/Phoenix")
    assert start == now
    assert end == datetime(2026, 11, 10, 12, tzinfo=UTC)


def test_next_window_after_end() -> None:
    now = datetime(2026, 11, 10, 14, tzinfo=UTC)  # 07:00 in Phoenix
    start, end = cli.next_window(now, time(19), time(5), "America/Phoenix")
    assert start == datetime(2026, 11, 11, 2, tzinfo=UTC)
    assert end - start == timedelta(hours=10)


class FakeDaemonClient:
    def __init__(self, now: datetime, error: Exception | None = None) -> None:
        self.clock = FakeClock(now)
        self.nights: list[tuple[datetime, datetime]] = []
        self.error = error

    async def night_once(
        self, start: datetime, end: datetime
    ) -> UploadSummary:
        self.nights.append((start, end))
        self.clock.advance(end - self.clock.now())
        if self.error:
            raise self.error
        return UploadSummary()

    async def upload(self) -> UploadSummary:
        return UploadSummary()


async def test_daemon_waits_for_next_night() -> None:
    now = datetime(2026, 11, 10, 6, tzinfo=UTC)  # 23:00 in Phoenix
    client = FakeDaemonClient(now)
    waits: list[timedelta] = []

    async def wait(delay: timedelta) -> bool:
        waits.append(delay)
        return len(waits) > 1

    await cli.daemon_loop(client, time(19), time(5), "America/Phoenix", wait)
    assert client.nights == [(now, datetime(2026, 11, 10, 12, tzinfo=UTC))]
    assert waits[1] == timedelta(hours=14)


async def test_daemon_survives_errors() -> None:
    now = datetime(2026, 11, 10, 6, tzinfo=UTC)
    client = FakeDaemonClient(
        now, error=LcftpApiError(ErrorCode.internal, "x", 500)
    )
    waits: list[timedelta] = []

    async def wait(delay: timedelta) -> bool:
        waits.append(delay)
        return len(waits) > 2

    await cli.daemon_loop(client, time(19), time(5), "America/Phoenix", wait)
    assert len(client.nights) == 1
    assert cli.UPLOAD_RETRY in waits
