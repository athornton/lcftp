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
import socket
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
    site_lon_lat: tuple[float, float] | None = None
    """Where the telescope believes it is (east-positive longitude)."""


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

    def abort(self) -> None:
        """Stop whatever the telescope is doing (goto, plate solve, stack)."""

    def list_subframes(self, target_name: str) -> list[str]:
        """Return the target's saved sub-frame FITS names, oldest first."""

    def download(self, target_name: str, name: str, dest: Path) -> Path:
        """Download one sub-frame to exactly ``dest``."""

    def park(self) -> None:
        """Park the arm and wait until it is home."""

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


def _lon_lat(value: Any) -> tuple[float, float] | None:
    if (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and all(isinstance(v, (int, float)) for v in value)
    ):
        return float(value[0]), float(value[1])
    return None


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

    def __init__(
        self, modules: Any = None, *, park_timeout_s: float = 180
    ) -> None:
        self._modules = modules
        self._park_timeout_s = park_timeout_s

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

    def _rpc(self, what: str, call: Any, *args: Any, **kwargs: Any) -> Any:
        """Call a JSON-RPC wrapper and raise if the firmware rejected it.

        seestarpy returns error replies (non-zero ``code``) instead of
        raising; event-style replies have no ``code`` and mean success.
        """
        response = self._do(what, call, *args, **kwargs)
        if isinstance(response, dict):
            code = response.get("code")
            error = response.get("error")
            if (code not in {None, 0}) or error:
                msg = f"Seestar rejected {what}: code {code}: {error}"
                raise SeestarControlError(msg)
        return response

    @staticmethod
    def _resolve(host: str) -> str:
        """Return the IPv4 address of ``host``.

        seestarpy accepts only IP addresses: given a hostname it sends
        nothing at all for most commands.
        """
        try:
            return socket.gethostbyname(host)
        except OSError as e:
            msg = f"Cannot resolve Seestar host {host}: {e}"
            raise SeestarControlError(msg) from e

    def connect(self, host: str | None, key_path: Path | None) -> DeviceInfo:
        """Connect and identify the telescope."""
        ssp = self._ssp
        if host:
            ip = self._resolve(host)
            ssp.connection.DEFAULT_IP = ip
            # Nested seestarpy calls (listing a folder, say) accept only
            # addresses in AVAILABLE_IPS, which UDP discovery fills.  The
            # scope does not always answer discovery, so register it here.
            ssp.connection.AVAILABLE_IPS[host] = ip
        if key_path is not None:
            self._do("set_key_path", ssp.auth.set_key_path, str(key_path))
        has_key = bool(ssp.auth.KEY_PATH) or key_path is not None
        try:
            state = _result(
                self._rpc(
                    "get_device_state",
                    ssp.raw.get_device_state,
                    keys=["device", "location_lon_lat"],
                )
            )
            device = state["device"]
            info = DeviceInfo(
                product_model=str(device["product_model"]),
                firmware=str(device["firmware_ver_string"]),
                focal_len_mm=_optional_float(device.get("focal_len")),
                site_lon_lat=_lon_lat(state.get("location_lon_lat")),
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
        self._rpc("open", self._ssp.ui.open)

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
            self._rpc("read coordinates", ssp.raw.scope_get_equ_coord)
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
        # Not ui.exposure: it adds expert_mode=True, which firmware 9.75
        # rejects with code 109 "unexpected param".
        self._rpc(
            "set exposure",
            self._ssp.raw.set_setting,
            exp_ms={"stack_l": seconds * 1000},
        )

    def set_save_subframes(self, *, enabled: bool) -> None:
        """Keep each accepted sub-frame."""
        self._rpc(
            "set stack setting",
            self._ssp.raw.set_setting,
            stack={"save_discrete_ok_frame": enabled},
        )

    def start_stack(self) -> None:
        """Start stacking."""
        self._rpc("start stack", self._ssp.ui.start_stack, restart=True)

    def stack_info(self) -> StackInfo:
        """Read stacking progress from the app state."""
        state = _result(
            self._rpc("read app state", self._ssp.raw.iscope_get_app_state)
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
        self._rpc("stop stack", self._ssp.raw.iscope_stop_view, stage="Stack")

    def abort(self) -> None:
        """Stop the whole view: goto, plate solve and stack."""
        self._rpc("stop view", self._ssp.raw.iscope_stop_view)

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
        """Park the arm and wait until the firmware reports it home."""
        outcome = self._do(
            "park",
            self._ssp.ui.goto,
            "park",
            verbose=False,
            timeout=self._park_timeout_s,
        )
        state = outcome.get("state") if isinstance(outcome, dict) else None
        if state != "complete":
            error = outcome.get("error") if isinstance(outcome, dict) else None
            msg = f"Seestar park ended {state}: {error}"
            raise SeestarControlError(msg)

    def disconnect(self) -> None:
        """Close connections."""
        self._do("disconnect", self._ssp.connection.close_connections)
