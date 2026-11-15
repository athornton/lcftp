#!/usr/bin/env python3
"""Record a short real Seestar session for the driver's replay tests.

Run on a machine on the same network as the telescope, at night::

    uv run python drivers/seestar/scripts/record-seestar-session.py \
        --host 192.168.1.50 --key ~/.seestarpy/seestar.pem \
        --lat 40.0 --lon -111.0 --ra-hours 5.588 --dec -5.39

``--no-goto`` records only connection and settings (safe in daylight).
The output directory receives ``session.jsonl`` and one
``<frame>.header.json`` per sub-frame; pixel data is not kept.
"""

import argparse
import contextlib
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from lcftp_driver_seestar.control import (
    SeestarControl,
    SeestarControlError,
    SeestarPyControl,
)
from lcftp_driver_seestar.recording import RecordingControl
from lcftp_driver_seestar.safety import sun_altitude_deg

DEFAULT_OUT = Path(__file__).parent.parent / "tests" / "data" / "recorded"
TARGET = "lcftp_recording"
SUN_LIMIT_DEG = -12.0
STACK_TIMEOUT_FACTOR = 4


def record_session(
    control: SeestarControl,
    out_dir: Path,
    *,
    host: str | None,
    key_path: Path | None,
    ra_hours: float | None,
    dec_deg: float | None,
    subs: int,
    exposure_s: int,
    goto: bool,
    lat_deg: float | None,
    lon_deg: float | None,
    now: datetime,
    poll_s: float = 5,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    """Run and record a scripted session; return the log path."""
    target: tuple[float, float] | None = None
    if goto:
        if ra_hours is None or dec_deg is None:
            msg = "A goto session needs --ra-hours and --dec"
            raise SystemExit(msg)
        if lat_deg is None or lon_deg is None:
            msg = "A goto session needs --lat and --lon (Sun safety check)"
            raise SystemExit(msg)
        sun = sun_altitude_deg(now, lat_deg=lat_deg, lon_deg=lon_deg)
        if sun > SUN_LIMIT_DEG:
            msg = (
                f"Refusing to slew: the Sun is at {sun:.1f}° (limit"
                f" {SUN_LIMIT_DEG}°). Use --no-goto in daylight."
            )
            raise SystemExit(msg)
        target = (ra_hours, dec_deg)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / "session.jsonl"
    log.unlink(missing_ok=True)
    for old in out_dir.glob("*.header.json"):
        old.unlink()
    rec = RecordingControl(control, log)
    rec.connect(host, key_path)
    if target is None:
        rec.set_exposure(exposure_s)
        rec.disconnect()
        return log
    rec.open_arm()
    rec.set_exposure(exposure_s)
    rec.set_save_subframes(enabled=True)
    rec.goto(TARGET, *target, 180)
    rec.start_stack()
    deadline = time.monotonic() + STACK_TIMEOUT_FACTOR * subs * exposure_s
    try:
        while rec.stack_info().stacked_frames < subs:
            if time.monotonic() > deadline:
                break
            sleep(poll_s)
    finally:
        with contextlib.suppress(SeestarControlError):
            rec.stop_stack()
    with tempfile.TemporaryDirectory() as tmp:
        for name in rec.list_subframes(TARGET):
            rec.download(TARGET, name, Path(tmp) / "frame.fits")
    rec.park()
    rec.disconnect()
    return log


def main() -> None:
    """Record a session from a real telescope."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host")
    parser.add_argument("--key", type=Path)
    parser.add_argument("--lat", type=float)
    parser.add_argument("--lon", type=float)
    parser.add_argument("--ra-hours", type=float)
    parser.add_argument("--dec", type=float)
    parser.add_argument("--subs", type=int, default=3)
    parser.add_argument("--exposure", type=int, default=10)
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
        now=datetime.now(tz=UTC),
    )
    print(f"Recorded {log}")  # noqa: T201


if __name__ == "__main__":
    main()
