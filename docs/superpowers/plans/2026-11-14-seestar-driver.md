# Seestar Driver (Milestone A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `seestar` telescope driver plugin that runs LCFTP observation
plans on a real ZWO Seestar, first an **S30 Plus**, using `seestarpy`.

**Architecture:** The driver is its own workspace package,
`drivers/seestar` (`lcftp-driver-seestar`), registered under the existing
`lcftp.drivers` entry point. It talks to the telescope only through a
small `SeestarControl` protocol. One implementation wraps `seestarpy`; a
recording wrapper captures a real session to JSONL; a replay
implementation feeds that recording back to the tests. As with the
ALeRCE work, behavior is pinned against a recorded session from the real
telescope rather than against guesses. The driver is **model-agnostic**:
it reads focal length, pixel size and frame geometry from the device and
its FITS headers rather than from a model table. That matters because
`seestarpy` 0.8.0 knows the S50 and S30 Pro but not the S30 Plus.

**Tech Stack:** Python ≥ 3.13 (the client's floor), asyncio
(`asyncio.to_thread` around the synchronous `seestarpy`), `seestarpy`
0.8.x, astropy, pydantic v2, pytest.

**Spec:** `docs/superpowers/specs/2026-11-09-lcftp-milestone-b-design.md`
§2 "Milestone A" ("a `seestar` driver plugin built on seestarpy and
end-to-end operation on real hardware … primarily a matter of writing
one driver") and the `TelescopeDriver` contract in
`client/src/lcftp_client/drivers/base.py` (with
`drivers/testing.py:assert_driver_contract`). There is no separate
Milestone A spec; decisions below that go beyond §2 are flagged
**Decision**.

## Facts this plan relies on (gathered 2026-11-14)

- **`seestarpy` 0.8.0** (PyPI) is **GPLv3**, needs Python ≥ 3.11, is
  synchronous, has no `py.typed`, and keeps the target IP in a module
  global (`seestarpy.connection.DEFAULT_IP`). It uses JSON-RPC on port
  4700 (port 4702 for the S30 Pro wide camera), HTTP on port 80 for
  downloads, and SMB on port 445 for listing and deleting.
- **Calls used:**
  - `ui.goto((ra_hours, dec_deg), verbose=False, timeout=…)` returns
    `{state, cur_ra_dec, dist_deg, error, code}`.
  - `ui.exposure(seconds)` accepts 2, 5, 10, 20, 30 or 60 s.
  - `ui.start_stack(restart=True)`, `raw.iscope_stop_view()`,
    `raw.get_stack_info()`, `raw.get_stack_setting()` and
    `raw.set_stack_setting(...)`, whose `save_discrete_ok_frame` keeps
    each accepted sub-frame.
  - `raw.get_device_state()`: its `device` block has `product_model`,
    `firmware_ver_string` and `focal_len`.
  - `data.list_folder_contents(folder, "fit")` and
    `data.download_file(folder, name, dest)`. Sub-frames land in
    `MyWorks/<target>_sub/Light_<target>_<exp>s_<filter>_<stamp>.fit`.
  - `ui.goto("park")` and `ui.close()`.
- **Firmware 7.18+ requires an RSA challenge-response handshake.** The key
  is not shipped with `seestarpy`; users extract it from ZWO's Android APK
  (`python -m seestarpy.extract_pem`) and point `SEESTAR_KEY_PATH` at it.
  Without a key, authentication is *silently skipped* and newer firmware
  then refuses commands.
- **S30 Plus** (ZWO spec table): the telephoto is 30 mm f/5.3 with 160 mm
  focal length, a 1/1.8" SC485SL sensor, 1440×2560 portrait output and a
  3° field of view (the original S30 is 2.46°). The wide-angle camera is
  6 mm f/1.75, a 1/2" IMX586, with a 43.5° field. The pixel size is
  unconfirmed: the 3° field over 2560 px implies ≈ 4.2″/px, ≈ 3.3 µm at
  160 mm. `seestarpy` assumes 2.9 µm for every model, so Task 6 checks
  this against the recorded FITS `XPIXSZ`.

## Decisions for Adam to confirm before execution

1. **Licensing (Decision).** `seestarpy` is GPLv3 and LCFTP is MIT.
   Importing GPL code makes the combined, distributed program GPL. The
   plan therefore puts the driver in a **separate package,
   `drivers/seestar`, licensed GPL-3.0-or-later**. `lcftp-client` stays
   MIT, has no dependency on it, and finds it only through the entry
   point. The client's Docker image and the RSP notebook do not install
   it by default. If you would rather avoid GPL entirely, the alternative
   is to write the JSON-RPC client ourselves; Task 2's `SeestarControl`
   seam makes that a swap of one class, but it means reimplementing the
   authentication handshake.
2. **Authentication key (Decision).** LCFTP never ships, downloads or
   extracts ZWO's key. The driver takes `key_path` in its config (or
   honors `SEESTAR_KEY_PATH`). If the firmware is 7.18 or later and no
   key is configured, `connect()` fails with a clear error instead of
   continuing silently. Whether to extract the key from the APK you own
   is your decision; check ZWO's terms first.
3. **Telephoto only (Decision).** Plans are for faint point sources, so
   the driver uses the telephoto camera. The 43.5° wide camera is out of
   scope.
4. **Sub-frames, not stacks (Decision).** Each accepted sub-frame becomes
   one `CapturedFrame`. Science needs per-exposure provenance, and the
   on-board stack can be rebuilt later.

## Global Constraints

- Python ≥ 3.13 (this runs on the observatory Pi beside `lcftp-client`).
- Every `seestarpy` call goes through `asyncio.to_thread`. Only one
  driver instance talks to a telescope (the module global
  `DEFAULT_IP`), so calls are serialized with an `asyncio.Lock`.
- Pin `seestarpy>=0.8,<0.9`; its API changed between minor versions.
- mypy strict as for the rest of the repo. `seestarpy` gets a
  `[[tool.mypy.overrides]] ignore_missing_imports = true`, and only
  `SeestarPyControl` may touch untyped values. It narrows them at once.
- Frames are written only to `ctx.new_frame_path(frame_id)`. `park()` is
  safe at any time and repeatable. Hardware failure raises `DriverError`.
  Weather is reported as `weather_blocked=True`
  (`client/src/lcftp_client/drivers/base.py`).
- Recorded fixtures contain no Wi-Fi credentials, serial numbers, IP
  addresses or key material. The recorder scrubs `sn`, `ssid`, `ip`,
  `mac` and `bssid` values to fixed placeholders.
- Tasks marked **(hardware)** need the telescope and a person. This
  sandbox cannot reach a LAN device, so an executor stops at them.

## Review Focus

1. **Firmware ≥ 7.18 and no key.** `connect()` raises `DriverError`
   naming `key_path` and `SEESTAR_KEY_PATH`; it does not hang, and it
   does not "succeed" and then fail on the first goto. (Test in Task 5.)
2. **`not_after` passes mid-stack.** The stack is stopped, the frames
   already accepted are downloaded and returned, and nothing is left
   running. (Test in Task 5.)
3. **Goto ends `fail`, `timeout` or `cancel`, or `dist_deg` exceeds a
   quarter of the FOV.** Raise `DriverError`, never start the stack.
   (Test in Task 5.)
4. **The Sun is up.** The driver refuses any goto while the Sun's
   altitude at the site is above −6°, even if a plan asks: this is
   hardware safety, not just planning. (Test in Task 5.)
5. **The idle socket drops** (the firmware closes port 4700 after
   ~15.5 s idle). Waiting for a 60 s sub-frame must keep polling
   `get_stack_info` at most every 5 s, and one failed poll is retried
   once before raising. (Test in Task 5.)

## File Structure

```
drivers/seestar/
  pyproject.toml          # lcftp-driver-seestar, GPL-3.0-or-later, entry point
  LICENSE                 # GPLv3 text
  README.md               # install, key, config, licensing note
  src/lcftp_driver_seestar/
    __init__.py
    config.py             # SeestarDriverConfig
    control.py            # SeestarControl protocol + SeestarPyControl
    recording.py          # RecordingControl, ReplayControl, scrub()
    driver.py             # SeestarDriver, create_driver
  scripts/record-seestar-session.py
  tests/
    config_test.py  control_test.py  recording_test.py
    driver_test.py  recorded_test.py  recorder_test.py
    data/recorded/        # Task 4 output
```

Root `pyproject.toml`: add `drivers/seestar` to `[tool.uv.workspace]
members` and `[tool.uv.sources]`; add its tests to `noxfile.py`
`_TEST_DIRS` and the typing session.

---

### Task 0: Bring the telescope up (hardware, Adam)

- [ ] Unbox, charge and update the firmware in the Seestar app.
- [ ] Put the telescope in station mode on the same Wi-Fi as the Pi or
  laptop; give it a DHCP reservation.
- [ ] Note `product_model` and `firmware_ver_string`:
  `uv run --with 'seestarpy>=0.8,<0.9' python -c "import seestarpy as s; s.connection.DEFAULT_IP='<ip>'; print(s.raw.get_device_state(keys=['device'])['result']['device'])"`.
  If this returns an authentication error or nothing, the firmware needs
  the key (Decision 2).
- [ ] Report the model string, the firmware version and whether a key
  was needed. Those values go into Task 1's tests.

### Task 1: Package skeleton, config, entry point

**Files:** create `drivers/seestar/{pyproject.toml,LICENSE,README.md}`,
`src/lcftp_driver_seestar/{__init__.py,config.py}`, `tests/config_test.py`;
modify root `pyproject.toml`, `noxfile.py`.

**Interfaces — produces:**
- `SeestarDriverConfig(BaseModel, frozen, extra="forbid")` with these
  fields:
  - `host: str | None = None` (`None` means `seestarpy` discovery)
  - `key_path: Path | None = None`
  - `sub_exposure_s: Literal[2, 5, 10, 20, 30, 60] = 10`
  - `default_total_exposure_s: float = 60` (used when the hint has only
    `target_snr`)
  - `goto_timeout_s: float = 180`
  - `max_pointing_error_fov: float = 0.25`
  - `poll_interval_s: float = 5`
  - `sun_altitude_limit_deg: float = -6`
  - `site_lat_deg: float`, `site_lon_deg: float`, `site_elevation_m: float = 0`
- Entry point `[project.entry-points."lcftp.drivers"] seestar =
  "lcftp_driver_seestar.driver:create_driver"`; `create_driver` is added
  in Task 5. Until then it is a stub that raises `NotImplementedError`.

- [ ] **Step 1: Failing tests.**
  - `test_defaults`
  - `test_rejects_unsupported_sub_exposure` (15 s → `ValidationError`)
  - `test_entry_point_registered` (`"seestar"` in the `lcftp.drivers`
    group)
- [ ] **Step 2:** `uv run pytest drivers/seestar/tests -q` → FAIL (package missing).
- [ ] **Step 3: Implement.** The package declares `license =
  "GPL-3.0-or-later"` and depends on `lcftp-client`, `lcftp-models`,
  `seestarpy>=0.8,<0.9` and `astropy`. `lcftp-client` must **not** gain
  a dependency on it.
- [ ] **Step 4:** tests pass; `uv run nox -s lint typing` passes.
- [ ] **Step 5: Commit** — `feat(seestar): driver package skeleton and config`.

### Task 2: The `SeestarControl` seam and its seestarpy implementation

**Files:** `src/lcftp_driver_seestar/control.py`, `tests/control_test.py`.

**Interfaces — produces** (all synchronous; the driver threads them):
```python
class GotoResult(BaseModel):  # frozen
    state: Literal["complete", "fail", "cancel", "timeout"]
    ra_hours: float | None
    dec_deg: float | None
    dist_deg: float | None
    error: str | None


class DeviceInfo(BaseModel):  # frozen
    product_model: str
    firmware: str
    focal_len_mm: float | None


class StackInfo(BaseModel):  # frozen
    stacked_frames: int
    dropped_frames: int
    running: bool


class SeestarControl(Protocol):
    def connect(
        self, host: str | None, key_path: Path | None
    ) -> DeviceInfo: ...
    def goto(
        self, ra_hours: float, dec_deg: float, timeout_s: float
    ) -> GotoResult: ...
    def set_exposure(self, seconds: int) -> None: ...
    def set_save_subframes(self, enabled: bool) -> None: ...
    def start_stack(self, target_name: str) -> None: ...
    def stack_info(self) -> StackInfo: ...
    def stop_stack(self) -> None: ...
    def list_subframes(
        self, target_name: str
    ) -> list[str]: ...  # file names, oldest first
    def download(self, target_name: str, name: str, dest: Path) -> Path: ...
    def park(self) -> None: ...
    def disconnect(self) -> None: ...
```
`SeestarPyControl` implements it over `seestarpy`. It maps each call
listed under "Facts"; `target_name` becomes the folder
`f"{target_name}_sub"`. `connect` sets `connection.DEFAULT_IP`, calls
`auth.set_key_path` when given a key, and calls `get_device_state`.
Errors are wrapped as `SeestarControlError(Exception)`. `StackInfo` and
`GotoResult` are built by narrowing the raw dicts.

- [ ] **Step 1: Failing tests.** `SeestarPyControl` is tested with
  `monkeypatch` on the `seestarpy` functions only to check the narrowing
  and mapping:
  - `goto` converts a `{state: "complete", cur_ra_dec: [h, d],
    dist_deg: x}` dict.
  - `list_subframes` keeps only `.fit` names under `<target>_sub` and
    sorts them by the timestamp in the name.
  - A `seestarpy` exception becomes `SeestarControlError`.
  - `connect` with firmware `"7.18"` or later and no key raises
    `SeestarControlError`, with a message containing `key_path` and
    `SEESTAR_KEY_PATH` (Review Focus 1). Compare versions numerically, so
    7.9 is less than 7.18.

  The real stack-info field names are confirmed in Task 6. Until then the
  narrowing reads `stacked_frame` and `dropped_frame`, after
  `raw.get_stack_info`.
- [ ] **Step 2–4:** red, implement, green; lint and typing.
- [ ] **Step 5: Commit** — `feat(seestar): SeestarControl seam over seestarpy`.

### Task 3: Recording, replay, and the session recorder

**Files:** `src/lcftp_driver_seestar/recording.py`,
`scripts/record-seestar-session.py`, `tests/recording_test.py`,
`tests/recorder_test.py`.

**Interfaces — produces:**
- `RecordingControl(inner: SeestarControl, log: Path)` passes every call
  through and appends one JSON line per call:
  `{"method", "args", "result" | "error"}`, results via `model_dump`.
  `download` also writes `<name>.header.json`, the primary header as a
  list of `[key, value, comment]` cards.
- `ReplayControl(log: Path)` answers calls in recorded order. It raises
  `AssertionError` naming both methods when the call sequence diverges.
  `download` writes a 32×32 `int16` FITS whose header is the recorded
  header with `NAXIS1`/`NAXIS2` set to 32 and `CRPIX*` scaled to match,
  so WCS checks still mean something.
- `scrub(value: JsonValue) -> JsonValue` replaces the values of keys
  `sn`, `ssid`, `ip`, `mac`, `bssid`, `passwd` and `password` (at any
  depth) with `"REDACTED"`.
- Script: `uv run python drivers/seestar/scripts/record-seestar-session.py
  --host <ip> [--key <pem>] --ra-hours <h> --dec <d> [--subs 3]
  [--exposure 10] [--out drivers/seestar/tests/data/recorded] [--no-goto]`.
  It runs connect, set_exposure, set_save_subframes, goto, start_stack,
  polls `stack_info` until `--subs` frames are stacked, then stop_stack,
  list_subframes, downloads every new frame, parks and disconnects.
  `--no-goto` (daytime) does only connect, set_exposure, park and
  disconnect. Without `--no-goto`, the script refuses to run while the
  Sun is above −12° at the device's reported site, or at `--lat`/`--lon`.

- [ ] **Step 1: Failing tests.**
  - A record-then-replay round trip through a scripted in-memory fake
    reproduces the same results.
  - A diverging call raises an error naming both methods.
  - `scrub` redacts nested keys.
  - The replayed FITS has a celestial WCS when the recorded header had
    one.
  - The recorder script, driven by the fake, writes `session.jsonl` and
    the header JSON, and refuses to goto when a frozen clock puts the Sun
    up.
- [ ] **Step 2–4:** red, implement, green; lint and typing.
- [ ] **Step 5: Commit** — `feat(seestar): record and replay telescope sessions`.

### Task 4: Record a real session (hardware, Adam, at night)

- [ ] Pick a bright, high, easy target. Pass its ICRS RA in **hours** and
  Dec in degrees.
- [ ] Run the recorder with `--subs 3 --exposure 10`, plus `--key` if
  Task 0 showed the firmware needs one.
- [ ] Check `drivers/seestar/tests/data/recorded/` holds
  `session.jsonl`, one `.header.json` per sub-frame, no FITS pixel data,
  and no unscrubbed serial number, IP or SSID:
  `grep -rEi '"(sn|ssid|ip)": "[^R]' drivers/seestar/tests/data/recorded`
  must print nothing.
- [ ] Commit — `test(seestar): recorded S30 Plus session`, then push.

### Task 5: The driver

**Files:** `src/lcftp_driver_seestar/driver.py`, `tests/driver_test.py`.

**Interfaces:**
- **Consumes:** `SeestarControl`, `SeestarDriverConfig`,
  `TelescopeDriver`, `ObservationContext`, `DriverResult`,
  `CapturedFrame`, `DriverError` and `assert_driver_contract` from
  `lcftp_client.drivers`.
- **Produces:**
  - `SeestarDriver(config, control: SeestarControl | None = None)`, with
    `name == "seestar"` and `version` set to
    `f"{lcftp-driver-seestar version}+seestarpy{version}"`.
  - `create_driver(config: Mapping[str, JsonValue]) -> SeestarDriver`.
  - `exposure_plan(hint: ExposureHint, sub_s: int, default_total_s: float) -> int`,
    the number of sub-frames: `max(1, ceil(total / sub_s))`.

Behavior of `observe(item, ctx)`:
1. If the Sun's altitude at the site is above `sun_altitude_limit_deg`,
   raise `DriverError`. Use `ctx.clock` for the time and astropy
   `get_sun` with `AltAz`.
2. `goto(item.candidate.ra / 15, item.candidate.dec, goto_timeout_s)`.
   Raise `DriverError` if `state != "complete"` or if
   `dist_deg > max_pointing_error_fov * ctx.fov_arcmin / 60`.
3. Call `set_exposure(sub_exposure_s)` and `set_save_subframes(True)`.
   The target name is `f"lcftp_{item.item_id}"`, which keeps the folder
   unique per item. Then `start_stack(name)`.
4. Every `poll_interval_s`, using `ctx.clock`, read `stack_info`. Stop
   when `stacked_frames` reaches `exposure_plan(...)` or when the clock
   passes `item.not_after`. Retry one failed poll once; a second failure
   raises `DriverError`. **Always** call `stop_stack()`, in a `finally`.
5. `list_subframes(name)`, then `download` each file to
   `ctx.new_frame_path(uuid4())`.
6. For each frame, read `DATE-OBS`, `EXPTIME` and `FILTER` from its
   header, and RA/Dec from `CRVAL1`/`CRVAL2`, falling back to
   `RA`/`DEC` and then to the goto result.
7. If the header has no celestial WCS, add a TAN WCS. Center it on the
   pointing, with scale `206.265 * XPIXSZ / focal_len_mm` arcsec per
   pixel (`XPIXSZ` from the header, focal length from `DeviceInfo`) and
   no rotation. Set `driver_metadata["wcs"] = "approximate-pointing"`;
   otherwise set it to `"device"`.
8. If the clock passed `not_after` and no frame was accepted
   (`stacked_frames == 0`, `dropped_frames > 0`), return
   `DriverResult(frames=[], weather_blocked=True)`.
9. Every frame's `driver_metadata` includes `product_model`, `firmware`,
   `dropped_frames` and the goto `dist_deg`.

`park()` calls `stop_stack()` (errors ignored), then `park()`; it is
idempotent. `connect()` and `disconnect()` delegate. All control calls
go through `asyncio.to_thread` under one `asyncio.Lock`.

- [ ] **Step 1: Failing tests**, using a scripted fake `SeestarControl`
  and a frozen or advancing `Clock`:
  - `test_contract`: `assert_driver_contract` passes.
  - `test_refuses_goto_with_sun_up` (Review Focus 4).
  - `test_failed_goto_raises_without_stacking` and
    `test_large_pointing_error_raises` (Review Focus 3).
  - `test_not_after_stops_stack_and_returns_frames` (Review Focus 2):
    the clock passes `not_after` after 2 of 6 sub-frames, so 2 frames
    come back and `stop_stack` was called.
  - `test_stop_stack_called_when_poll_raises`.
  - `test_one_failed_poll_is_retried` (Review Focus 5).
  - `test_all_frames_dropped_is_weather_blocked`.
  - `test_missing_key_on_new_firmware_fails_connect` (Review Focus 1;
    the fake raises the error from Task 2).
  - `test_exposure_plan`: 60 s total with 10 s subs gives 6;
    3 s total with 10 s subs gives 1; `target_snr` only, with a 60 s
    default, gives 6.
  - `test_wcs_added_when_header_lacks_one`: the scale equals
    `206.265 * XPIXSZ / focal` and the metadata says
    `approximate-pointing`.
  - `test_park_is_idempotent_and_safe_before_connect`.
- [ ] **Step 2–4:** red, implement, green; lint and typing.
- [ ] **Step 5: Commit** — `feat(seestar): Seestar telescope driver`.

### Task 6: Replay the recorded session

**Files:** `tests/recorded_test.py`; modify `control.py` (field names)
and the README only as the recording requires.

- [ ] **Step 1: Failing tests** against `ReplayControl` and
  `data/recorded/session.jsonl`:
  - `test_recorded_session_passes_contract`.
  - `test_recorded_frames_have_celestial_wcs`.
  - `test_recorded_pixel_scale_matches_s30_plus_fov`: from the recorded
    `.header.json` (the original `NAXIS2` and `XPIXSZ`, not the 32×32
    replay) and the recorded `focal_len_mm`, the long axis times the
    scale is within 10% of 3°, if `product_model` contains
    `"S30 Plus"`.
  - `test_recorded_device_identity` checks that `product_model` and
    `firmware` match what Task 0 reported (pinned literals).
- [ ] **Step 2:** run; record each failure.
- [ ] **Step 3: Correct the narrowing in `SeestarPyControl`** (field
  names, units) to match the recording. Record each correction as a
  ruling. **Stop and ask** if the FITS headers carry no pointing at all
  (no `RA`/`DEC`/`CRVAL*`), because frames would then need plate solving,
  which is a separate decision.
- [ ] **Step 4:** green; lint and typing.
- [ ] **Step 5:** in the README, record the S30 Plus pixel size and
  scale, the folder and file naming, and whether the device writes a WCS.
- [ ] **Step 6: Commit** — `fix(seestar): match recorded S30 Plus session`.

### Task 7: First light (hardware, Adam)

- [ ] Register the observatory with `driver = "seestar"`,
  `fov_arcmin = 180` and `limiting_mag` around 13–14 to start, then
  adjust once you have data. Set the site coordinates.
- [ ] Put the driver config in the client config (host, key_path,
  site), then run `lcftp-client` for a short availability window.
- [ ] Acceptance:
  - at least one item is observed;
  - its frames upload and verify;
  - their FITS headers carry the `LCFTP*` provenance keywords and a
    celestial WCS;
  - the telescope parks at the end of the window and on Ctrl-C.
- [ ] Note the results in a new `docs/superpowers/notes/seestar-first-light.md`.

---

## Not in this plan

- Plate solving for frames without a usable WCS (Task 6 stops if needed).
- The wide-angle camera, mosaics, autofocus scheduling, dew heater
  control, and the LP/dark filter positions.
- A non-GPL JSON-RPC client (see Decision 1).
