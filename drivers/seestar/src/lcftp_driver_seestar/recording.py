"""Record a real Seestar session and replay it in tests.

`RecordingControl` wraps a real `SeestarControl` and appends one JSON
line per call to a log. Downloaded frames contribute only their primary
header (``<name>.header.json``), never pixel data. `ReplayControl` answers
the same calls, in the same order, from that log. Identifying values
(serial numbers, network details, key paths) are scrubbed on the way in.
"""

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits
from pydantic import BaseModel, JsonValue

from .control import (
    DeviceInfo,
    GotoResult,
    SeestarControl,
    SeestarControlError,
    StackInfo,
)

__all__ = ["REDACTED", "RecordingControl", "ReplayControl", "scrub"]

REDACTED = "REDACTED"
_SCRUB_KEYS = frozenset(
    {
        "sn",
        "ssid",
        "ip",
        "mac",
        "bssid",
        "passwd",
        "password",
        "host",
        "key_path",
    }
)
_NULLED_KEYS = frozenset({"site_lon_lat"})
"""Keys whose values are dropped (set to null) rather than redacted."""

_IDENTIFYING_HEADER = re.compile(
    r"^(SN|SERIAL.*|.*LAT.*|.*LON.*|OBSGEO.*|SITE.*|HOST.*|IP)$"
)
"""FITS keywords that could reveal the site or the device."""

_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")

_STRUCTURAL = frozenset(
    {
        "SIMPLE",
        "BITPIX",
        "NAXIS",
        "NAXIS1",
        "NAXIS2",
        "EXTEND",
        "BZERO",
        "BSCALE",
    }
)
_REPLAY_SIZE = 32


def scrub(value: JsonValue) -> JsonValue:
    """Replace identifying values (and IP addresses in text), at any depth."""
    if isinstance(value, dict):
        scrubbed: dict[str, JsonValue] = {}
        for k, v in value.items():
            if k.lower() in _NULLED_KEYS:
                scrubbed[k] = None
            elif k.lower() in _SCRUB_KEYS:
                scrubbed[k] = REDACTED
            else:
                scrubbed[k] = scrub(v)
        return scrubbed
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return _IPV4.sub(REDACTED, value)
    return value


def _jsonable(value: Any) -> JsonValue:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def header_cards(path: Path) -> list[list[JsonValue]]:
    """Return a FITS primary header as scrubbed ``[key, value, comment]``."""
    with fits.open(path) as hdul:
        header = hdul[0].header
        cards: list[list[JsonValue]] = []
        for card in header.cards:
            value = card.value
            if not isinstance(value, (str, int, float, bool)):
                value = None
            if (
                card.keyword.lower() in _SCRUB_KEYS
                or _IDENTIFYING_HEADER.match(card.keyword)
            ):
                value = REDACTED
            elif isinstance(value, str):
                value = _IPV4.sub(REDACTED, value)
            cards.append([card.keyword, value, card.comment])
    return cards


class RecordingControl:
    """Pass calls through to a real control and log them."""

    def __init__(self, inner: SeestarControl, log: Path) -> None:
        self._inner = inner
        self._log = log

    def _record(
        self, method: str, args: dict[str, Any], call: Callable[[], Any]
    ) -> Any:
        entry: dict[str, JsonValue] = {
            "method": method,
            "args": scrub({k: _jsonable(v) for k, v in args.items()}),
        }
        try:
            result = call()
        except SeestarControlError as e:
            entry["error"] = scrub(str(e))
            self._write(entry)
            raise
        entry["result"] = scrub(_jsonable(result))
        self._write(entry)
        return result

    def _write(self, entry: dict[str, JsonValue]) -> None:
        with self._log.open("a") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")

    def connect(self, host: str | None, key_path: Path | None) -> DeviceInfo:
        """Connect and log the device identity."""
        return self._record(
            "connect",
            {"host": host, "key_path": key_path},
            lambda: self._inner.connect(host, key_path),
        )

    def open_arm(self) -> None:
        """Unpark the arm."""
        self._record("open_arm", {}, self._inner.open_arm)

    def goto(
        self,
        target_name: str,
        ra_hours: float,
        dec_deg: float,
        timeout_s: float,
    ) -> GotoResult:
        """Slew and log the outcome."""
        return self._record(
            "goto",
            {
                "target_name": target_name,
                "ra_hours": ra_hours,
                "dec_deg": dec_deg,
                "timeout_s": timeout_s,
            },
            lambda: self._inner.goto(
                target_name, ra_hours, dec_deg, timeout_s
            ),
        )

    def set_exposure(self, seconds: int) -> None:
        """Set the exposure."""
        self._record(
            "set_exposure",
            {"seconds": seconds},
            lambda: self._inner.set_exposure(seconds),
        )

    def set_save_subframes(self, *, enabled: bool) -> None:
        """Set sub-frame saving."""
        self._record(
            "set_save_subframes",
            {"enabled": enabled},
            lambda: self._inner.set_save_subframes(enabled=enabled),
        )

    def start_stack(self) -> None:
        """Start stacking."""
        self._record("start_stack", {}, self._inner.start_stack)

    def stack_info(self) -> StackInfo:
        """Log stacking progress."""
        return self._record("stack_info", {}, self._inner.stack_info)

    def stop_stack(self) -> None:
        """Stop stacking."""
        self._record("stop_stack", {}, self._inner.stop_stack)

    def abort(self) -> None:
        """Stop the whole view."""
        self._record("abort", {}, self._inner.abort)

    def list_subframes(self, target_name: str) -> list[str]:
        """Log the sub-frame listing."""
        return self._record(
            "list_subframes",
            {"target_name": target_name},
            lambda: self._inner.list_subframes(target_name),
        )

    def download(self, target_name: str, name: str, dest: Path) -> Path:
        """Download a frame and keep its header (not its pixels)."""

        def call() -> Path:
            path = self._inner.download(target_name, name, dest)
            cards = header_cards(path)
            header_path = self._log.parent / f"{name}.header.json"
            header_path.write_text(json.dumps(cards, indent=1) + "\n")
            return path

        result: Path = self._record(
            "download", {"target_name": target_name, "name": name}, call
        )
        return result

    def park(self) -> None:
        """Park."""
        self._record("park", {}, self._inner.park)

    def disconnect(self) -> None:
        """Disconnect."""
        self._record("disconnect", {}, self._inner.disconnect)


def _write_small_frame(cards: list[list[Any]], dest: Path) -> None:
    """Write a 32x32 frame whose WCS covers the same sky as the original."""
    original = {card[0]: card[1] for card in cards}
    scale = (
        int(original.get("NAXIS1") or _REPLAY_SIZE) / _REPLAY_SIZE,
        int(original.get("NAXIS2") or _REPLAY_SIZE) / _REPLAY_SIZE,
    )
    header = fits.Header()
    for key, value, comment in cards:
        if key in _STRUCTURAL:
            continue
        if key in {"COMMENT", "HISTORY"}:
            header.append((key, value or ""))
            continue
        header.append((key, value, comment))
    for axis in (1, 2):
        factor = scale[axis - 1]
        if f"CRPIX{axis}" in header:
            crpix = float(header[f"CRPIX{axis}"])
            header[f"CRPIX{axis}"] = (crpix - 0.5) / factor + 0.5
        if f"CDELT{axis}" in header:
            header[f"CDELT{axis}"] = float(header[f"CDELT{axis}"]) * factor
        for row in (1, 2):
            key = f"CD{row}_{axis}"
            if key in header:
                header[key] = float(header[key]) * factor
    data = np.zeros((_REPLAY_SIZE, _REPLAY_SIZE), dtype=np.uint16)
    fits.PrimaryHDU(data, header).writeto(dest, overwrite=True)


class ReplayControl:
    """Answer `SeestarControl` calls from a recorded session log."""

    def __init__(self, log: Path) -> None:
        self._dir = log.parent
        self._entries = [
            json.loads(line) for line in log.read_text().splitlines() if line
        ]
        self._next = 0

    def _take(self, method: str) -> Any:
        if self._next >= len(self._entries):
            msg = f"replay exhausted: driver called {method}"
            raise AssertionError(msg)
        entry = self._entries[self._next]
        if entry["method"] != method:
            msg = (
                f"replay diverged at call {self._next}: recorded"
                f" {entry['method']}, driver called {method}"
            )
            raise AssertionError(msg)
        self._next += 1
        if "error" in entry:
            raise SeestarControlError(entry["error"])
        return entry.get("result")

    @property
    def remaining(self) -> int:
        """Recorded calls not yet replayed."""
        return len(self._entries) - self._next

    def connect(self, host: str | None, key_path: Path | None) -> DeviceInfo:
        """Replay the device identity."""
        return DeviceInfo.model_validate(self._take("connect"))

    def open_arm(self) -> None:
        """Replay unparking."""
        self._take("open_arm")

    def goto(
        self,
        target_name: str,
        ra_hours: float,
        dec_deg: float,
        timeout_s: float,
    ) -> GotoResult:
        """Replay a slew."""
        return GotoResult.model_validate(self._take("goto"))

    def set_exposure(self, seconds: int) -> None:
        """Replay setting the exposure."""
        self._take("set_exposure")

    def set_save_subframes(self, *, enabled: bool) -> None:
        """Replay setting sub-frame saving."""
        self._take("set_save_subframes")

    def start_stack(self) -> None:
        """Replay starting the stack."""
        self._take("start_stack")

    def stack_info(self) -> StackInfo:
        """Replay stacking progress."""
        return StackInfo.model_validate(self._take("stack_info"))

    def stop_stack(self) -> None:
        """Replay stopping the stack."""
        self._take("stop_stack")

    def abort(self) -> None:
        """Replay stopping the view."""
        self._take("abort")

    def list_subframes(self, target_name: str) -> list[str]:
        """Replay the sub-frame listing."""
        return [str(name) for name in self._take("list_subframes")]

    def download(self, target_name: str, name: str, dest: Path) -> Path:
        """Write a small frame carrying the recorded header."""
        self._take("download")
        cards = json.loads((self._dir / f"{name}.header.json").read_text())
        _write_small_frame(cards, dest)
        return dest

    def park(self) -> None:
        """Replay parking."""
        self._take("park")

    def disconnect(self) -> None:
        """Replay disconnecting."""
        self._take("disconnect")
