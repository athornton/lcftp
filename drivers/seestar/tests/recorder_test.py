import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from tests_support import FakeControl

from lcftp_driver_seestar.recording import ReplayControl

SCRIPT = Path(__file__).parent.parent / "scripts" / "record-seestar-session.py"
NIGHT = datetime(2026, 11, 15, 6, 0, tzinfo=UTC)  # 11pm in Utah
NOON = datetime(2026, 11, 15, 19, 0, tzinfo=UTC)
SITE = {"lat_deg": 40.0, "lon_deg": -111.0}


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("record_seestar", SCRIPT)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _methods(log: Path) -> list[str]:
    return [
        json.loads(line)["method"] for line in log.read_text().splitlines()
    ]


def test_night_session_records_everything(tmp_path: Path) -> None:
    log = _load().record_session(
        FakeControl(),
        tmp_path,
        host="192.168.1.50",
        key_path=None,
        ra_hours=5.5,
        dec_deg=-5.4,
        subs=3,
        exposure_s=10,
        goto=True,
        now=NIGHT,
        poll_s=0,
        **SITE,
    )
    assert log == tmp_path / "session.jsonl"
    assert _methods(log) == [
        "connect",
        "open_arm",
        "set_exposure",
        "set_save_subframes",
        "goto",
        "start_stack",
        "stack_info",
        "stack_info",
        "stack_info",
        "stop_stack",
        "list_subframes",
        "download",
        "park",
        "disconnect",
    ]
    headers = list(tmp_path.glob("*.header.json"))
    assert len(headers) == 1
    pixels = [p for p in tmp_path.rglob("*") if p.suffix in {".fit", ".fits"}]
    assert pixels == []
    assert ReplayControl(log).remaining == 14


def test_refuses_goto_in_daylight(tmp_path: Path) -> None:
    control = FakeControl()
    with pytest.raises(SystemExit, match="Sun"):
        _load().record_session(
            control,
            tmp_path,
            host=None,
            key_path=None,
            ra_hours=5.5,
            dec_deg=-5.4,
            subs=3,
            exposure_s=10,
            goto=True,
            now=NOON,
            poll_s=0,
            **SITE,
        )
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
        now=NOON,
        poll_s=0,
        lat_deg=None,
        lon_deg=None,
    )
    assert _methods(log) == ["connect", "set_exposure", "disconnect"]
