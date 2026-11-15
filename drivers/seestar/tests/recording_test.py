import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from astropy import units as u
from astropy.coordinates import AltAz, EarthLocation, get_sun
from astropy.io import fits
from astropy.time import Time
from astropy.utils import iers
from astropy.wcs import WCS
from pydantic import JsonValue
from tests_support import NAME, FakeControl

from lcftp_driver_seestar.control import (
    SeestarControlError,
)
from lcftp_driver_seestar.recording import (
    RecordingControl,
    ReplayControl,
    scrub,
)
from lcftp_driver_seestar.safety import sun_altitude_deg


def _session(control: RecordingControl | ReplayControl, tmp: Path) -> list:
    results: list = [
        control.connect("192.168.1.50", Path("/keys/seestar.pem")),
        control.goto("t", 5.5, -5.4, 60),
        control.stack_info(),
        control.list_subframes("t"),
    ]
    with pytest.raises(SeestarControlError, match="already stopped"):
        control.stop_stack()
    path = control.download("t", NAME, tmp / "frame.fits")
    results.append(path.name)
    control.park()
    return results


def test_record_then_replay_round_trip(tmp_path: Path) -> None:
    log = tmp_path / "rec" / "session.jsonl"
    log.parent.mkdir()
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    recorded = _session(RecordingControl(FakeControl(), log), tmp_path / "a")
    replayed = _session(ReplayControl(log), tmp_path / "b")
    # The device's site is deliberately not recorded (it is a home address).
    recorded[0] = recorded[0].model_copy(update={"site_lon_lat": None})
    assert replayed == recorded


def test_recording_scrubs_identifying_values(tmp_path: Path) -> None:
    log = tmp_path / "session.jsonl"
    (tmp_path / "f").mkdir()
    _session(RecordingControl(FakeControl(), log), tmp_path / "f")
    text = log.read_text()
    assert "192.168.1.50" not in text
    assert "seestar.pem" not in text
    header = (tmp_path / f"{NAME}.header.json").read_text()
    assert "a3497936" not in header
    assert "REDACTED" in header


def test_replay_divergence_names_both_methods(tmp_path: Path) -> None:
    log = tmp_path / "session.jsonl"
    RecordingControl(FakeControl(), log).park()
    with pytest.raises(AssertionError) as e:
        ReplayControl(log).start_stack()
    assert "park" in str(e.value)
    assert "start_stack" in str(e.value)


@pytest.mark.filterwarnings("ignore::astropy.wcs.FITSFixedWarning")
def test_replayed_frame_is_small_with_equivalent_wcs(tmp_path: Path) -> None:
    log = tmp_path / "session.jsonl"
    (tmp_path / "a").mkdir()
    recorder = RecordingControl(FakeControl(), log)
    original = recorder.download("t", NAME, tmp_path / "a" / "orig.fits")
    replayed = ReplayControl(log).download("t", NAME, tmp_path / "small.fits")
    with fits.open(replayed) as hdul:
        header = hdul[0].header
        assert hdul[0].data.shape == (32, 32)
    assert header["EXPTIME"] == 10.0
    small = WCS(header)
    with fits.open(original) as hdul:
        big = WCS(hdul[0].header)
    assert small.has_celestial
    corner_small = small.pixel_to_world(31.5, 31.5)
    corner_big = big.pixel_to_world(1439.5, 2559.5)
    assert corner_small.separation(corner_big).deg < 0.01


def test_scrub_nested() -> None:
    value: JsonValue = {
        "a": [{"sn": "x", "ok": 1}],
        "IP": "10.0.0.2",
        "keep": "y",
    }
    assert scrub(value) == {
        "a": [{"sn": "REDACTED", "ok": 1}],
        "IP": "REDACTED",
        "keep": "y",
    }


def test_header_json_is_cards(tmp_path: Path) -> None:
    log = tmp_path / "session.jsonl"
    (tmp_path / "a").mkdir()
    RecordingControl(FakeControl(), log).download(
        "t", NAME, tmp_path / "a" / "f.fits"
    )
    cards = json.loads((tmp_path / f"{NAME}.header.json").read_text())
    assert ["EXPTIME", 10.0, ""] in cards
    assert any(card[0] == "NAXIS2" and card[1] == 2560 for card in cards)


@pytest.mark.parametrize(
    "when",
    [
        datetime(2026, 11, 15, 19, 0, tzinfo=UTC),
        datetime(2026, 11, 15, 3, 0, tzinfo=UTC),
        datetime(2026, 6, 21, 12, 0, tzinfo=UTC),
    ],
)
def test_sun_altitude_matches_astropy(when: datetime) -> None:
    lat, lon = 40.0, -111.0
    with iers.conf.set_temp("auto_download", False):
        site = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=0 * u.m)
        t = Time(when)
        expected = get_sun(t).transform_to(AltAz(obstime=t, location=site))
    got = sun_altitude_deg(when, lat_deg=lat, lon_deg=lon)
    assert got == pytest.approx(expected.alt.deg, abs=0.5)


def test_recorded_errors_and_site_are_scrubbed(tmp_path: Path) -> None:
    class Failing(FakeControl):
        def start_stack(self) -> None:
            msg = "send_command to 192.168.1.50 failed: timed out"
            raise SeestarControlError(msg)

    log = tmp_path / "session.jsonl"
    (tmp_path / "a").mkdir()
    rec = RecordingControl(Failing(), log)
    with pytest.raises(SeestarControlError):
        rec.start_stack()
    rec.download("t", NAME, tmp_path / "a" / "f.fits")
    assert "192.168.1.50" not in log.read_text()
    header = (tmp_path / f"{NAME}.header.json").read_text()
    assert "40.1234" not in header
    assert "111.5678" not in header
