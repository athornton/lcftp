# LCFTP status, 2026-11-15

Where things stand after Milestone B, the ALeRCE live-fields work, and the
start of Milestone A (the Seestar driver). Start a new session by reading
this file, then the plan for whatever comes next.

## Done and on `main`

### ALeRCE live fields

Plan: `docs/superpowers/plans/2026-11-13-alerce-live-fields.md`. The
original plan and status files were lost (never committed), so the plan
was rewritten on 2026-11-14 and executed in full.

- **Field names.** ALeRCE field names live in two tables in
  `src/lcftp/brokers/alerce.py`. A renamed field fails the whole poll as
  `BrokerUnavailableError` and names the missing field. A null value is
  treated as a data skip, not as schema drift.
- **Live data.** Recorded responses are in `tests/data/alerce/recorded/`,
  and the tests replay them. `scripts/record-alerce-fixtures.py`
  re-records them.
- **Per-classifier rows.** ALeRCE lists each object once per classifier.
  The plugin keeps the newest classifier's row, drops `bogus` objects
  before fetching their detections, and fills
  `Candidate.classification` and `score`.
- **Findings** are in `docs/superpowers/notes/alerce-lsst-spike.md`
  ("Live results"): about 54k object rows a day, and most objects are far
  fainter than amateur limits.
- **Lint.** Ruff no longer requires per-file copyright notices
  (`CPY001` off; the LICENSE covers every file).

### Seestar driver: Tasks 1, 2, 3 and 5 of 7

Plan: `docs/superpowers/plans/2026-11-14-seestar-driver.md`.

- The package is `drivers/seestar` (`lcftp-driver-seestar`), licensed
  **GPL-3.0-or-later** because it builds on GPLv3 `seestarpy` 0.8. The
  MIT client finds it only through the `lcftp.drivers` entry point.
  `seestar` is the driver name.
- Telescope: a Seestar **S30 Plus** on firmware **9.75**. That firmware
  needs the RSA authentication key, which LCFTP never ships or extracts
  (set `key_path` or `SEESTAR_KEY_PATH`).
- Everything is tested only against fakes. No code has talked to the
  real telescope yet.
- The deliberate deviations from the plan are recorded in the commit
  messages of `23cba49..489792e`. The main ones:
  - Slews use `goto_target` (which plate-solves and names the folder);
    stack progress comes from `iscope_get_app_state`; sub-frame saving
    goes through `raw.set_setting`.
  - The recorder drives the real driver through the
    `assert_driver_contract` sequence, so a recording replays call for
    call.
  - Site longitude is east-positive. The driver refuses to connect if
    the configured site is more than 1° from the telescope's own
    location.

## Next steps (need the telescope and a person)

1. **Task 0 (rest).** Station mode on home Wi-Fi with a DHCP reservation;
   get the authentication key (check ZWO's terms first).
2. **Task 4.** On a clear night, record a session on a bright star
   (`--lon` is east-positive, west negative):
   ```bash
   uv run python drivers/seestar/scripts/record-seestar-session.py --host <ip> --key <pem> --lat <deg N> --lon <deg E> --ra-hours <h> --dec <deg>
   ```
   Commit `drivers/seestar/tests/data/recorded/` (`session.jsonl`,
   `meta.json`, `*.header.json`). Recordings hold no pixels, serial
   numbers, IP addresses or site location. Check the headers for
   anything else identifying before committing.
3. **Task 6.** Replay the recording. Correct the response field names in
   `SeestarPyControl` to match it. Confirm the S30 Plus pixel scale
   (3° across 2560 px) and whether the firmware works in J2000 or JNow
   coordinates (see the plan).
4. **Task 7.** First light with `lcftp-client` on a real plan:
   `fov_arcmin = 180`, and start `limiting_mag` around 13–14.

## Known small issues (deferred)

- **Seestar driver:**
  - `seestarpy` prints every command to stdout (`VERBOSE_LEVEL` 1).
  - `poll_interval_s` has no upper bound below the ~15 s idle-socket
    timeout.
  - The stack's "running" flag is ignored.
  - Replay rescaling ignores rotated or distorted WCS.
  - The driver's "arm open" flag can go stale if the arm is parked
    outside the driver.
  - A `park()` that raises skips `disconnect()` in the runner's cleanup.
- **ALeRCE:**
  - A coordinate of exactly 0.0 falls back to the object's mean position.
  - The recorder fetches duplicate objects twice and leaves stale pages
    behind on re-record.
  - Classifier rows of one object split across pages are not merged.
  - Negative-flux skips log at DEBUG only.

## Working in the cloud sandbox

- **Docker.** Not running by default. Start it with `dockerd &` before
  running the database tests (`testcontainers`).
- **Pre-commit cache.** It often loses its hook executables
  ("Executable … not found"). Run `rm -rf ~/.cache/pre-commit` and then
  `uv run nox -s lint` again.
- **ALeRCE.** `api-lsst.alerce.online` is on the environment's
  allowed-domains list. `seestarpy.readthedocs.io` is not; PyPI is
  reachable, so the `seestarpy` source can be read from its sdist.
