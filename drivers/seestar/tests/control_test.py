from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lcftp_driver_seestar.control import (
    DeviceInfo,
    GotoResult,
    SeestarControlError,
    SeestarPyControl,
    StackInfo,
    firmware_requires_key,
)

DEVICE = {
    "product_model": "Seestar S30 Plus",
    "firmware_ver_string": "9.75",
    "focal_len": 160.0,
}


def _fake(**overrides: Any) -> SimpleNamespace:
    """Stand-ins for the seestarpy modules SeestarPyControl uses."""
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def rec(name: str, result: Any = None) -> Any:
        def fn(*args: Any, **kwargs: Any) -> Any:
            calls.append((name, args, kwargs))
            value = overrides.get(name, result)
            if isinstance(value, Exception):
                raise value
            return value(*args, **kwargs) if callable(value) else value

        return fn

    ns = SimpleNamespace(calls=calls)
    ns.connection = SimpleNamespace(
        DEFAULT_IP="10.0.0.1", close_connections=rec("close_connections")
    )
    ns.auth = SimpleNamespace(
        KEY_PATH=overrides.get("KEY_PATH", "/keys/seestar.pem"),
        set_key_path=rec("set_key_path"),
    )
    ns.raw = SimpleNamespace(
        get_device_state=rec(
            "get_device_state", {"result": {"device": DEVICE}}
        ),
        scope_get_equ_coord=rec(
            "scope_get_equ_coord", {"result": {"ra": 5.5, "dec": -5.4}}
        ),
        set_setting=rec("set_setting", {"result": 0}),
        iscope_get_app_state=rec(
            "iscope_get_app_state",
            {
                "result": {
                    "View": {
                        "stage": "Stack",
                        "Stack": {
                            "state": "working",
                            "stacked_frame": 3,
                            "dropped_frame": 1,
                        },
                    }
                }
            },
        ),
        iscope_stop_view=rec("iscope_stop_view", {"result": 0}),
    )
    ns.ui = SimpleNamespace(
        open=rec("open", {"result": 0}),
        goto_target=rec("goto_target", {"state": "complete", "error": None}),
        exposure=rec("exposure", 10),
        start_stack=rec("start_stack", {"result": 0}),
        close=rec("close", {"result": 0}),
    )
    ns.data = SimpleNamespace(
        list_folder_contents=rec(
            "list_folder_contents",
            {
                "Light_t_10.0s_IRCUT_20261115-031012.fit": 7000,
                "Light_t_10.0s_IRCUT_20261115-031000.fit": 7000,
                "Light_t_10.0s_IRCUT_20261115-031000_thn.jpg": 20,
            },
        ),
        download_file=rec("download_file"),
    )
    return ns


def _control(ns: SimpleNamespace) -> SeestarPyControl:
    return SeestarPyControl(modules=ns)


def _names(ns: SimpleNamespace) -> list[str]:
    return [name for name, _, _ in ns.calls]


def test_connect_reads_device_and_sets_host() -> None:
    ns = _fake()
    info = _control(ns).connect("192.168.1.50", None)
    assert info == DeviceInfo(
        product_model="Seestar S30 Plus", firmware="9.75", focal_len_mm=160.0
    )
    assert ns.connection.DEFAULT_IP == "192.168.1.50"


def test_connect_sets_key_path(tmp_path: Path) -> None:
    ns = _fake()
    key = tmp_path / "seestar.pem"
    _control(ns).connect(None, key)
    assert ("set_key_path", (str(key),), {}) in ns.calls


@pytest.mark.parametrize(
    ("version", "expected"),
    [("7.9", False), ("7.18", True), ("9.75", True), ("6.70", False)],
)
def test_firmware_requires_key(version: str, expected: bool) -> None:  # noqa: FBT001
    assert firmware_requires_key(version) is expected


def test_connect_without_key_on_new_firmware_fails() -> None:
    ns = _fake(KEY_PATH=None)
    with pytest.raises(SeestarControlError) as e:
        _control(ns).connect(None, None)
    assert "key_path" in str(e.value)
    assert "SEESTAR_KEY_PATH" in str(e.value)


def test_connect_failure_without_key_mentions_key() -> None:
    ns = _fake(KEY_PATH=None, get_device_state=ConnectionError("refused"))
    with pytest.raises(SeestarControlError) as e:
        _control(ns).connect(None, None)
    assert "SEESTAR_KEY_PATH" in str(e.value)


def test_goto_uses_named_target_and_measures_pointing() -> None:
    ns = _fake()
    result = _control(ns).goto("lcftp_item1", 5.5, -5.39, 120)
    assert result.state == "complete"
    assert (result.ra_hours, result.dec_deg) == (5.5, -5.4)
    assert result.dist_deg == pytest.approx(0.01, abs=1e-6)
    (call,) = [c for c in ns.calls if c[0] == "goto_target"]
    assert call[1][:3] == ("lcftp_item1", 5.5, -5.39)
    assert call[2]["verbose"] is False
    assert call[2]["timeout"] == 120


def test_goto_accepts_list_coordinates() -> None:
    ns = _fake(scope_get_equ_coord={"result": [5.5, -5.4]})
    result = _control(ns).goto("t", 5.5, -5.4, 120)
    assert result.dist_deg == pytest.approx(0.0, abs=1e-9)


def test_failed_goto_reports_state_without_pointing() -> None:
    ns = _fake(goto_target={"state": "timeout", "error": "slow"})
    result = _control(ns).goto("t", 5.5, -5.4, 120)
    assert result == GotoResult(
        state="timeout",
        ra_hours=None,
        dec_deg=None,
        dist_deg=None,
        error="slow",
    )


def test_stack_info_narrows_app_state() -> None:
    info = _control(_fake()).stack_info()
    assert info == StackInfo(stacked_frames=3, dropped_frames=1, running=True)


def test_stack_info_missing_counts_are_zero() -> None:
    ns = _fake(iscope_get_app_state={"result": {"View": {"stage": "Stack"}}})
    info = _control(ns).stack_info()
    assert info == StackInfo(stacked_frames=0, dropped_frames=0, running=False)


def test_save_subframes_and_stop() -> None:
    ns = _fake()
    control = _control(ns)
    control.set_save_subframes(True)
    control.stop_stack()
    assert (
        "set_setting",
        (),
        {"stack": {"save_discrete_ok_frame": True}},
    ) in ns.calls
    assert ("iscope_stop_view", (), {"stage": "Stack"}) in ns.calls


def test_list_subframes_fits_only_oldest_first() -> None:
    ns = _fake()
    names = _control(ns).list_subframes("t")
    assert names == [
        "Light_t_10.0s_IRCUT_20261115-031000.fit",
        "Light_t_10.0s_IRCUT_20261115-031012.fit",
    ]
    assert ns.calls[-1][1] == ("t_sub", "fit")


def test_download_moves_file_to_dest(tmp_path: Path) -> None:
    def download(folder: str, name: str, dest: str) -> str:
        path = Path(dest) / name
        path.write_bytes(b"SIMPLE")
        return str(path)

    ns = _fake(download_file=download)
    dest = tmp_path / "frames" / "abc.fits"
    dest.parent.mkdir()
    result = _control(ns).download("t", "Light_t.fit", dest)
    assert result == dest
    assert dest.read_bytes() == b"SIMPLE"
    assert list(dest.parent.iterdir()) == [dest]


def test_errors_are_wrapped() -> None:
    ns = _fake(start_stack=OSError("socket closed"))
    with pytest.raises(SeestarControlError, match="socket closed"):
        _control(ns).start_stack()


def test_park_and_disconnect() -> None:
    ns = _fake()
    control = _control(ns)
    control.open_arm()
    control.set_exposure(10)
    control.park()
    control.disconnect()
    assert _names(ns) == ["open", "exposure", "close", "close_connections"]
