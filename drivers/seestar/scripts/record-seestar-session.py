#!/usr/bin/env python3
"""Record a short real Seestar session for the driver's replay tests.

Run on a machine on the same network as the telescope, at night::

    uv run python drivers/seestar/scripts/record-seestar-session.py \
        --host 192.168.1.50 --key ~/.seestarpy/seestar.pem \
        --lat 40.0 --lon -111.0 --ra-hours 5.588 --dec -5.39

Longitude is east-positive (west is negative). The script runs the real
`SeestarDriver` through the same sequence as ``assert_driver_contract``
(connect, observe one item, park twice, disconnect) with every telescope
call recorded, so the tests can replay the session through the driver.

``--no-goto`` records only connection and settings (safe in daylight).
The output directory receives ``session.jsonl``, ``meta.json`` and one
``<frame>.header.json`` per sub-frame; pixel data is not kept.
"""

import argparse
import asyncio
import contextlib
import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from lcftp_client.clock import Clock, SystemClock
from lcftp_client.drivers.base import ObservationContext
from lcftp_client.drivers.testing import assert_driver_contract
from lcftp_driver_seestar.config import SeestarDriverConfig, SubExposure
from lcftp_driver_seestar.control import SeestarControl, SeestarPyControl
from lcftp_driver_seestar.driver import SeestarDriver
from lcftp_driver_seestar.recording import RecordingControl
from lcftp_driver_seestar.safety import sun_altitude_deg
from lcftp_models.candidate import BrokerRef, Candidate
from lcftp_models.plan import ExposureHint, PlanItem

DEFAULT_OUT = Path(__file__).parent.parent / "tests" / "data" / "recorded"
SUN_LIMIT_DEG = -12.0
"""Stricter than the driver's default, for an attended first session."""

ITEM_TIME_FACTOR = 4
"""Allow this many times the total exposure (plus slack) for the item."""


def recording_item(meta: dict[str, Any]) -> PlanItem:
    """Rebuild the plan item a recording observes from ``meta.json``."""
    start = datetime.fromisoformat(meta["started_at"])
    total = meta["subs"] * meta["exposure_s"]
    candidate = Candidate(
        dia_object_id=0,
        sources=[BrokerRef(broker="recording", broker_object_id="0")],
        latest_alert_id=None,
        latest_alert_time=start,
        ra=meta["ra_hours"] * 15,
        dec=meta["dec_deg"],
        mag=0.0,
        band="r",
        first_seen=start,
        last_updated=start,
    )
    slack = timedelta(
        seconds=ITEM_TIME_FACTOR * total + meta["goto_timeout_s"]
    )
    return PlanItem(
        item_id="recording",
        candidate=candidate,
        not_before=start,
        not_after=start + slack,
        suggested_filter=None,
        exposure_hint=ExposureHint(total_exposure_s=total),
        priority=0,
    )


def recording_context(
    frames: Path, clock: Clock, meta: dict[str, Any]
) -> ObservationContext:
    """Build the observation context a recording uses."""

    def new_path(frame_id: UUID) -> Path:
        return frames / f"{frame_id}.fits"

    return ObservationContext(
        plan_id="recording",
        observatory_id="recording",
        fov_arcmin=meta["fov_arcmin"],
        new_frame_path=new_path,
        clock=clock,
    )


def replay_config(meta: dict[str, Any]) -> SeestarDriverConfig:
    """Driver configuration for replaying a recording.

    The recording does not keep the site, so replay disables the Sun check
    (the real session already passed it).
    """
    return SeestarDriverConfig(
        site_lat_deg=0,
        site_lon_deg=0,
        sun_altitude_limit_deg=90,
        sub_exposure_s=meta["exposure_s"],
        poll_interval_s=meta["poll_s"],
        goto_timeout_s=meta["goto_timeout_s"],
    )


def record_session(
    control: SeestarControl,
    out_dir: Path,
    *,
    host: str | None,
    key_path: Path | None,
    ra_hours: float | None,
    dec_deg: float | None,
    subs: int,
    exposure_s: SubExposure,
    goto: bool,
    lat_deg: float | None,
    lon_deg: float | None,
    clock: Clock,
    fov_arcmin: float = 180,
    poll_s: float = 5,
    goto_timeout_s: float = 300,
) -> Path:
    """Run and record a session; return the log path."""
    if goto:
        if ra_hours is None or dec_deg is None:
            msg = "A goto session needs --ra-hours and --dec"
            raise SystemExit(msg)
        if lat_deg is None or lon_deg is None:
            msg = "A goto session needs --lat and --lon (Sun safety check)"
            raise SystemExit(msg)
        sun = sun_altitude_deg(clock.now(), lat_deg=lat_deg, lon_deg=lon_deg)
        if sun > SUN_LIMIT_DEG:
            msg = (
                f"Refusing to slew: the Sun is at {sun:.1f}° (limit"
                f" {SUN_LIMIT_DEG}°). Use --no-goto in daylight."
            )
            raise SystemExit(msg)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / "session.jsonl"
    log.unlink(missing_ok=True)
    for old in out_dir.glob("*.header.json"):
        old.unlink()
    rec = RecordingControl(control, log)
    if not goto or lat_deg is None or lon_deg is None:
        rec.connect(host, key_path)
        rec.set_exposure(exposure_s)
        rec.disconnect()
        return log
    meta = {
        "started_at": clock.now().isoformat(),
        "ra_hours": ra_hours,
        "dec_deg": dec_deg,
        "subs": subs,
        "exposure_s": exposure_s,
        "fov_arcmin": fov_arcmin,
        "poll_s": poll_s,
        "goto_timeout_s": goto_timeout_s,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    config = SeestarDriverConfig(
        site_lat_deg=lat_deg,
        site_lon_deg=lon_deg,
        host=host,
        key_path=key_path,
        sub_exposure_s=exposure_s,
        poll_interval_s=poll_s,
        goto_timeout_s=goto_timeout_s,
        sun_altitude_limit_deg=SUN_LIMIT_DEG,
    )
    driver = SeestarDriver(config, control=rec)
    with tempfile.TemporaryDirectory() as frames:
        asyncio.run(_run(driver, meta, Path(frames), clock))
    return log


async def _run(
    driver: SeestarDriver, meta: dict[str, Any], frames: Path, clock: Clock
) -> None:
    ctx = recording_context(frames, clock, meta)
    try:
        await assert_driver_contract(driver, recording_item(meta), ctx)
    except BaseException:
        with contextlib.suppress(Exception):
            await driver.park()
        with contextlib.suppress(Exception):
            await driver.disconnect()
        raise


def main() -> None:
    """Record a session from a real telescope."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host")
    parser.add_argument("--key", type=Path)
    parser.add_argument("--lat", type=float, help="degrees north")
    parser.add_argument("--lon", type=float, help="degrees EAST (west < 0)")
    parser.add_argument("--ra-hours", type=float)
    parser.add_argument("--dec", type=float)
    parser.add_argument("--subs", type=int, default=3)
    parser.add_argument(
        "--exposure", type=int, default=10, choices=[2, 5, 10, 20, 30, 60]
    )
    parser.add_argument("--fov-arcmin", type=float, default=180)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-goto", action="store_true")
    args = parser.parse_args()
    log = record_session(
        SeestarPyControl(),
        args.out,
        host=args.host,
        key_path=args.key,
        ra_hours=args.ra_hours,
        dec_deg=args.dec,
        subs=args.subs,
        exposure_s=args.exposure,
        goto=not args.no_goto,
        lat_deg=args.lat,
        lon_deg=args.lon,
        clock=SystemClock(),
        fov_arcmin=args.fov_arcmin,
    )
    print(f"Recorded {log} at {datetime.now(tz=UTC).isoformat()}")  # noqa: T201


if __name__ == "__main__":
    main()
