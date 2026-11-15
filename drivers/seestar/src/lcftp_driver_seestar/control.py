"""The narrow interface the driver uses to operate a Seestar.

`SeestarControl` is everything the driver needs from the telescope.
`SeestarPyControl` implements it on top of seestarpy. It is the only code
that touches seestarpy's untyped responses, and it narrows them at once.
Recording and replay wrappers live in `lcftp_driver_seestar.recording`.
"""

import importlib
import math
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

__all__ = [
    "DeviceInfo",
    "GotoResult",
    "SeestarControl",
    "SeestarControlError",
    "SeestarPyControl",
    "StackInfo",
    "angular_distance_deg",
    "firmware_requires_key",
]

_AUTH_FIRMWARE = (7, 18)
_KEY_HELP = (
    "Seestar firmware 7.18 and later needs an authentication key: set"
    " key_path in the driver configuration or the SEESTAR_KEY_PATH"
    " environment variable"
)
_STAMP = re.compile(r"(\d{8}-\d{6})")


class SeestarControlError(Exception):
    """A Seestar command failed."""


class DeviceInfo(BaseModel):
    """Identity and optics reported by the telescope."""

    model_config = ConfigDict(frozen=True)

    product_model: str
    firmware: str
    focal_len_mm: float | None


class GotoResult(BaseModel):
    """Outcome of a slew, with where the mount actually ended up."""

    model_config = ConfigDict(frozen=True)

    state: Literal["complete", "fail", "cancel", "timeout"]
    ra_hours: float | None
    dec_deg: float | None
    dist_deg: float | None
    error: str | None


class StackInfo(BaseModel):
    """Progress of the current stack."""

    model_config = ConfigDict(frozen=True)

    stacked_frames: int
    dropped_frames: int
    running: bool


class SeestarControl(Protocol):
    """Synchronous telescope operations; the driver runs them in threads."""

    def connect(self, host: str | None, key_path: Path | None) -> DeviceInfo:
        """Connect (authenticating if needed) and identify the telescope."""

    def open_arm(self) -> None:
        """Unpark the arm so the telescope can slew."""

    def goto(
        self,
        target_name: str,
        ra_hours: float,
        dec_deg: float,
        timeout_s: float,
    ) -> GotoResult:
        """Slew and plate-solve onto a target; frames go to its folder."""

    def set_exposure(self, seconds: int) -> None:
        """Set the sub-frame exposure time."""

    def set_save_subframes(self, *, enabled: bool) -> None:
        """Keep (or stop keeping) each accepted sub-frame as a FITS file."""

    def start_stack(self) -> None:
        """Start stacking on the current target."""

    def stack_info(self) -> StackInfo:
        """Report stacking progress."""

    def stop_stack(self) -> None:
        """Stop stacking (the camera keeps running)."""

    def list_subframes(self, target_name: str) -> list[str]:
        """Return the target's saved sub-frame FITS names, oldest first."""

    def download(self, target_name: str, name: str, dest: Path) -> Path:
        """Download one sub-frame to exactly ``dest``."""

    def park(self) -> None:
        """Park the arm."""

    def disconnect(self) -> None:
        """Close connections to the telescope."""


def firmware_requires_key(version: str) -> bool:
    """Whether firmware ``version`` (e.g. ``"9.75"``) needs the auth key."""
    parts = tuple(int(p) for p in re.findall(r"\d+", version)[:2])
    return parts >= _AUTH_FIRMWARE


def angular_distance_deg(
    ra1_hours: float, dec1_deg: float, ra2_hours: float, dec2_deg: float
) -> float:
    """Great-circle distance between two positions, in degrees."""
    ra1, ra2 = math.radians(ra1_hours * 15), math.radians(ra2_hours * 15)
    d1, d2 = math.radians(dec1_deg), math.radians(dec2_deg)
    hav = (
        math.sin((d2 - d1) / 2) ** 2
        + math.cos(d1) * math.cos(d2) * math.sin((ra2 - ra1) / 2) ** 2
    )
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(hav))))


def _result(response: Any) -> Any:
    if isinstance(response, dict) and "result" in response:
        return response["result"]
    return response


def _coords(response: Any) -> tuple[float, float]:
    """Narrow ``scope_get_equ_coord`` (a dict, or a list on old firmware)."""
    value = _result(response)
    if isinstance(value, dict):
        return float(value["ra"]), float(value["dec"])
    return float(value[0]), float(value[1])


def _optional_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


class SeestarPyControl:
    """`SeestarControl` implemented with seestarpy.

    Parameters
    ----------
    modules
        Object with ``ui``, ``raw``, ``data``, ``auth`` and ``connection``
        attributes standing in for the seestarpy modules (for tests).
        By default seestarpy is imported on first use, because importing
        it broadcasts a discovery probe on the local network.
    """

    def __init__(self, modules: Any = None) -> None:
        self._modules = modules

    @property
    def _ssp(self) -> Any:
        if self._modules is None:
            self._modules = importlib.import_module("seestarpy")
        return self._modules

    def _do(self, what: str, call: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return call(*args, **kwargs)
        except Exception as e:
            msg = f"Seestar {what} failed: {e}"
            raise SeestarControlError(msg) from e

    def connect(self, host: str | None, key_path: Path | None) -> DeviceInfo:
        """Connect and identify the telescope."""
        ssp = self._ssp
        if host:
            ssp.connection.DEFAULT_IP = host
        if key_path is not None:
            self._do("set_key_path", ssp.auth.set_key_path, str(key_path))
        has_key = bool(ssp.auth.KEY_PATH) or key_path is not None
        try:
            response = ssp.raw.get_device_state(keys=["device"])
            device = _result(response)["device"]
            info = DeviceInfo(
                product_model=str(device["product_model"]),
                firmware=str(device["firmware_ver_string"]),
                focal_len_mm=_optional_float(device.get("focal_len")),
            )
        except Exception as e:
            msg = f"Cannot read Seestar device state: {e}"
            if not has_key:
                msg = f"{msg}. {_KEY_HELP}"
            raise SeestarControlError(msg) from e
        if not has_key and firmware_requires_key(info.firmware):
            msg = f"Firmware {info.firmware}: {_KEY_HELP}"
            raise SeestarControlError(msg)
        return info

    def open_arm(self) -> None:
        """Unpark the arm."""
        self._do("open", self._ssp.ui.open)

    def goto(
        self,
        target_name: str,
        ra_hours: float,
        dec_deg: float,
        timeout_s: float,
    ) -> GotoResult:
        """Slew and plate-solve onto a target, then read the pointing."""
        ssp = self._ssp
        outcome = self._do(
            "goto",
            ssp.ui.goto_target,
            target_name,
            ra_hours,
            dec_deg,
            verbose=False,
            timeout=timeout_s,
        )
        state = str(outcome.get("state"))
        error = outcome.get("error")
        error = str(error) if error is not None else None
        if state != "complete":
            if state not in {"fail", "cancel", "timeout"}:
                error = f"unexpected goto state {state!r}: {error}"
                state = "fail"
            return GotoResult(
                state=state,  # type: ignore[arg-type]
                ra_hours=None,
                dec_deg=None,
                dist_deg=None,
                error=error,
            )
        ra, dec = _coords(
            self._do("read coordinates", ssp.raw.scope_get_equ_coord)
        )
        return GotoResult(
            state="complete",
            ra_hours=ra,
            dec_deg=dec,
            dist_deg=angular_distance_deg(ra_hours, dec_deg, ra, dec),
            error=error,
        )

    def set_exposure(self, seconds: int) -> None:
        """Set the sub-frame exposure time."""
        self._do("set exposure", self._ssp.ui.exposure, seconds)

    def set_save_subframes(self, *, enabled: bool) -> None:
        """Keep each accepted sub-frame."""
        self._do(
            "set stack setting",
            self._ssp.raw.set_setting,
            stack={"save_discrete_ok_frame": enabled},
        )

    def start_stack(self) -> None:
        """Start stacking."""
        self._do("start stack", self._ssp.ui.start_stack, restart=True)

    def stack_info(self) -> StackInfo:
        """Read stacking progress from the app state."""
        state = _result(
            self._do("read app state", self._ssp.raw.iscope_get_app_state)
        )
        view = state.get("View", {}) if isinstance(state, dict) else {}
        stack = view.get("Stack", {})
        return StackInfo(
            stacked_frames=int(stack.get("stacked_frame") or 0),
            dropped_frames=int(stack.get("dropped_frame") or 0),
            running=(
                view.get("stage") == "Stack"
                and stack.get("state") == "working"
            ),
        )

    def stop_stack(self) -> None:
        """Stop stacking."""
        self._do("stop stack", self._ssp.raw.iscope_stop_view, stage="Stack")

    def list_subframes(self, target_name: str) -> list[str]:
        """List the target's sub-frame FITS files, oldest first."""
        contents = self._do(
            "list sub-frames",
            self._ssp.data.list_folder_contents,
            f"{target_name}_sub",
            "fit",
        )
        names = [n for n in contents if str(n).endswith(".fit")]

        def stamp(name: str) -> tuple[str, str]:
            found = _STAMP.search(name)
            return (found.group(1) if found else "", name)

        return sorted(names, key=stamp)

    def download(self, target_name: str, name: str, dest: Path) -> Path:
        """Download a sub-frame, then move it to ``dest``."""
        with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
            path = self._do(
                "download",
                self._ssp.data.download_file,
                f"{target_name}_sub",
                name,
                tmp,
            )
            shutil.move(str(path), dest)
        return dest

    def park(self) -> None:
        """Park the arm."""
        self._do("park", self._ssp.ui.close)

    def disconnect(self) -> None:
        """Close connections."""
        self._do("disconnect", self._ssp.connection.close_connections)
