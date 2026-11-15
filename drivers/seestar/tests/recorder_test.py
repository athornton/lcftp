import asyncio
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from tests_support import FakeControl

from lcftp_client.clock import FakeClock
from lcftp_client.drivers.base import DriverError
from lcftp_client.drivers.testing import assert_driver_contract
from lcftp_driver_seestar.control import GotoResult
from lcftp_driver_seestar.driver import SeestarDriver
from lcftp_driver_seestar.recording import ReplayControl

pytestmark = pytest.mark.filterwarnings("ignore::astropy.wcs.FITSFixedWarning")

SCRIPT = Path(__file__).parent.parent / "scripts" / "record-seestar-session.py"
NIGHT = datetime(2026, 11, 15, 6, 0, tzinfo=UTC)  # 11pm in Utah
NOON = datetime(2026, 11, 15, 19, 0, tzinfo=UTC)
SITE = {"lat_deg": 40.0, "lon_deg": -111.0}
CONTRACT = [
    "connect",
    "open_arm",
    "goto",
    "set_exposure",
    "set_save_subframes",
    "start_stack",
    "stack_info",
    "stack_info",
    "stack_info",
    "stop_stack",
    "list_subframes",
    "download",
    "abort",
    "park",
    "abort",
    "park",
    "disconnect",
]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("record_seestar", SCRIPT)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _methods(log: Path) -> list[str]:
    lines = log.read_text().splitlines()
    return [json.loads(line)["method"] for line in lines]


def _record(
    tmp_path: Path, control: FakeControl, when: datetime = NIGHT
) -> Path:
    log: Path = _load().record_session(
        control,
        tmp_path,
        host="192.168.1.50",
        key_path=None,
        ra_hours=5.5,
        dec_deg=-5.4,
        subs=3,
        exposure_s=10,
        goto=True,
        clock=FakeClock(when),
        poll_s=0.001,
        **SITE,
    )
    return log


def test_night_session_records_the_contract_sequence(tmp_path: Path) -> None:
    log = _record(tmp_path, FakeControl())
    assert log == tmp_path / "session.jsonl"
    assert _methods(log) == CONTRACT
    assert len(list(tmp_path.glob("*.header.json"))) == 1
    pixels = [p for p in tmp_path.rglob("*") if p.suffix in {".fit", ".fits"}]
    assert pixels == []
    meta = json.loads((tmp_path / "meta.json").read_text())
    assert meta["ra_hours"] == 5.5
    assert meta["subs"] == 3
    assert "lat_deg" not in meta
    assert "40.0" not in log.read_text()


async def test_recording_replays_through_driver(tmp_path: Path) -> None:
    script = _load()
    # The recorder runs its own event loop, so record from a thread.
    log = await asyncio.to_thread(_record, tmp_path / "rec", FakeControl())
    meta = json.loads((tmp_path / "rec" / "meta.json").read_text())
    replay = ReplayControl(log)
    driver = SeestarDriver(script.replay_config(meta), control=replay)
    clock = FakeClock(datetime.fromisoformat(meta["started_at"]))
    (tmp_path / "frames").mkdir()
    result = await assert_driver_contract(
        driver,
        script.recording_item(meta),
        script.recording_context(tmp_path / "frames", clock, meta),
    )
    assert len(result.frames) == 1
    assert replay.remaining == 0


def test_failed_goto_still_parks(tmp_path: Path) -> None:
    class Lost(FakeControl):
        def goto(
            self,
            target_name: str,
            ra_hours: float,
            dec_deg: float,
            timeout_s: float,
        ) -> GotoResult:
            return GotoResult(
                state="fail",
                ra_hours=None,
                dec_deg=None,
                dist_deg=None,
                error="plate solve failed",
            )

    with pytest.raises(DriverError, match="plate solve"):
        _record(tmp_path, Lost())
    methods = _methods(tmp_path / "session.jsonl")
    assert methods[-3:] == ["abort", "park", "disconnect"]


def test_refuses_goto_in_daylight(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="Sun"):
        _record(tmp_path, FakeControl(), NOON)
    assert not (tmp_path / "session.jsonl").exists()


def test_daytime_mode_skips_goto(tmp_path: Path) -> None:
    log = _load().record_session(
        FakeControl(),
        tmp_path,
        host=None,
        key_path=None,
        ra_hours=None,
        dec_deg=None,
        subs=3,
        exposure_s=10,
        goto=False,
        clock=FakeClock(NOON),
        lat_deg=None,
        lon_deg=None,
    )
    assert _methods(log) == ["connect", "set_exposure", "disconnect"]
