# ALeRCE Live Fields Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the ALeRCE broker plugin correct against the *live* ALeRCE LSST API, not just against the synthetic fixtures from the Milestone B spike, and make it loud when ALeRCE's response schema drifts.

**Architecture:** All knowledge of ALeRCE's field names moves into two
declarative tables (`OBJECT_FIELDS`, `DETECTION_FIELDS`) in
`lcftp.brokers.alerce`, resolved by one function that reports *which*
logical field is missing and *what keys were present*. A recorder script
captures real responses into `tests/data/alerce/recorded/`; tests replay
them through the existing stub-client pattern, and the field tables are
corrected to match what was recorded. A page on which every object fails
schema resolution raises `BrokerUnavailableError`, so drift shows up as
broker failures and backoff rather than as silent empty polls.

**Tech Stack:** Python 3.14, asyncio, `alerce` 2.4.0 client, pytest +
pytest-asyncio (`asyncio_mode = "auto"`), uv, nox.

**Spec:** There is no dedicated spec for this work. The plan argues from
`docs/superpowers/specs/2026-11-09-lcftp-milestone-b-design.md` §4.4
(brokers) and §10 risk 1, and from
`docs/superpowers/notes/alerce-lsst-spike.md` ("Consequence": the
synthetic fixtures *must be replaced with recorded responses once the
live spike runs*; finding 5: `oid` = `diaObjectId` is unverified).

## Global Constraints

- Every ALeRCE call passes `survey="lsst"` explicitly.
- Flux → magnitude stays `m = 31.4 − 2.5·log10(flux_nJy)` via
  `lcftp.photometry.flux_njy_to_ab_mag`; do not reimplement it.
- Blocking `alerce` calls stay behind `asyncio.to_thread` (the existing
  `AlerceBroker._call`).
- No new runtime dependencies. The recorder uses only the `alerce` client
  and the standard library.
- Strict TDD: every behavior change begins as a failing test.
- Recorded fixtures contain only public alert data returned by ALeRCE; no
  credentials, hostnames or proxy details go into them.
- `api-lsst.alerce.online` is **not reachable from the cloud sandbox**
  (proxy 403, confirmed again 2026-11-14). Task 4 must run on a machine
  with direct internet access.
- Lint and typing must pass: `uv run nox -s lint typing`.

## Review Focus

1. **`oid` arrives as a string or is not numeric.** A numeric string
   (`"170055001234567890"`) becomes that integer `dia_object_id`; a
   non-numeric one is skipped with a warning naming the oid, and the poll
   continues. (Test in Task 1.)
2. **`band` missing from a detection.** Today `str(None)` stores the band
   as the literal `"None"`. Missing band must be a schema miss, not a
   candidate. (Test in Task 1.)
3. **ALeRCE renames a field.** Every object on the page fails, and the
   poll raises `BrokerUnavailableError` naming the logical field and the
   keys it saw, instead of returning nothing. (Test in Task 1.)
4. **The server ignores the `lastmjd` window.** Objects whose `lastmjd`
   is before `since` are dropped before any detections call, so a broken
   server filter costs no extra calls. (Test in Task 2.)
5. **Recorded data contains `NaN` fluxes or null coordinates.** The
   replayed poll skips those objects rather than crashing. (Covered by
   Task 5's whole-recording replay test, which must run without raising.)

## File Structure

- Modify `src/lcftp/brokers/alerce.py`: field tables, resolver, schema
  error handling, `lastmjd` guard.
- Modify `tests/brokers/alerce_test.py`: tests for the above against the
  existing synthetic fixtures (which stay as-is; they cover edge cases).
- Create `scripts/record-alerce-fixtures.py`: live recorder.
- Create `tests/scripts/record_alerce_fixtures_test.py`: recorder test
  with a stub client.
- Delete `docs/superpowers/notes/alerce_spike.py` (superseded by the
  recorder).
- Create `tests/data/alerce/recorded/` (Task 4, by a person with network).
- Create `tests/brokers/alerce_recorded_test.py`: replay tests.
- Modify `docs/superpowers/notes/alerce-lsst-spike.md`: live results.

---

### Task 1: Declarative field tables and schema-drift errors

**Files:**
- Modify: `src/lcftp/brokers/alerce.py`
- Test: `tests/brokers/alerce_test.py`

**Interfaces:**
- Produces, all exported from `lcftp.brokers.alerce`:
  - `OBJECT_FIELDS: Mapping[str, tuple[str, ...]]` =
    `{"oid": ("oid",), "ra": ("meanra",), "dec": ("meandec",), "lastmjd": ("lastmjd",)}`
  - `DETECTION_FIELDS: Mapping[str, tuple[str, ...]]` =
    `{"mjd": ("mjd", "midpointMjdTai"), "flux": ("psfFlux",), "ra": ("ra",), "dec": ("dec",), "alert_id": ("measurement_id", "diaSourceId", "candid"), "band": ("band_name", "band")}`
  - `class MissingFieldError(ValueError)` with attributes
    `field: str` (logical name) and `keys: list[str]` (sorted keys of the
    record); `str()` contains both.
  - `resolve_field(record: Mapping[str, Any], fields: Mapping[str, tuple[str, ...]], logical: str, *, required: bool = True) -> Any`
    — first non-`None` value among the names; if none, raises
    `MissingFieldError` when `required`, else returns `None`.
- Replaces the private `_field`. Required logical fields: `oid`,
  detection `mjd`, `flux`, `band`, and a position (detection `ra`/`dec`,
  falling back to object `ra`/`dec`). `alert_id` is optional.

- [ ] **Step 1: Write the failing tests** in `tests/brokers/alerce_test.py`

```python
def test_resolve_field_reports_logical_name_and_keys() -> None:
    with pytest.raises(MissingFieldError) as e:
        resolve_field({"psf_flux": 1.0, "mjd": 1.0}, DETECTION_FIELDS, "flux")
    assert e.value.field == "flux"
    assert e.value.keys == ["mjd", "psf_flux"]
    assert "flux" in str(e.value) and "psf_flux" in str(e.value)


def test_resolve_field_optional_returns_none() -> None:
    assert (
        resolve_field({}, DETECTION_FIELDS, "alert_id", required=False) is None
    )


def test_resolve_field_uses_fallback_names() -> None:
    assert (
        resolve_field({"midpointMjdTai": 5.0}, DETECTION_FIELDS, "mjd") == 5.0
    )
```

Plus a `SchemaAlerce(StubAlerce)` subclass whose `query_detections`
returns the stub's detections with keys transformed per test, and:

- `test_missing_band_is_skipped_not_stored_as_none`: detections for oid
  1003 with `band_name` and `band` removed → 1003 absent from
  `poll(SINCE, 30.0)`; a WARNING mentions `1003` and `band`; no
  candidate has `band == "None"`.
- `test_string_oid_is_converted`: page-1 object `{"oid": "1003", ...}`
  (detections keyed `"1003"`) → candidate with `dia_object_id == 1003`.
- `test_non_numeric_oid_is_skipped`: an extra object
  `{"oid": "ZTF26abc", "meanra": 1.0, "meandec": 1.0}` whose detections
  are valid → skipped, WARNING mentions `ZTF26abc`, other expected
  candidates still yielded (`_expected(30.0)`).
- `test_renamed_field_on_every_object_raises_unavailable`: every
  detection's `psfFlux` renamed to `psf_flux` → `poll` raises
  `BrokerUnavailableError` whose message contains `flux` and `psf_flux`.
- `test_one_bad_object_does_not_raise`: only oid 1003 renamed → poll
  completes, 1003 absent, others present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/brokers/alerce_test.py -q`
Expected: FAIL — `ImportError` for `MissingFieldError`/`resolve_field`.

- [ ] **Step 3: Implement** in `src/lcftp/brokers/alerce.py`

Add the tables, `MissingFieldError` and `resolve_field` as specified.
Rewrite `_candidate` to use `resolve_field` throughout; convert `oid`
with `int(str(oid))` so numeric strings work and others raise
`ValueError`. In `poll`, have `_safe_candidate` return a sentinel (or a
small result type) distinguishing "skipped for `MissingFieldError`" from
"filtered out" and "converted". After `gather` on a page: if the page
was non-empty and **every** object was skipped for `MissingFieldError`,
raise `BrokerUnavailableError(f"ALeRCE response schema changed: {err}")`
using the first error. Keep the existing per-object WARNING; its message
now includes the `MissingFieldError` text.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/brokers -q`
Expected: PASS, all tests including the 18 pre-existing ones.

- [ ] **Step 5: Commit**

```bash
git add src/lcftp/brokers/alerce.py tests/brokers/alerce_test.py
git commit -m "feat(alerce): declarative field tables and schema-drift errors"
```

### Task 2: Client-side `lastmjd` guard

**Files:**
- Modify: `src/lcftp/brokers/alerce.py` (`_safe_candidate` / `_candidate`)
- Test: `tests/brokers/alerce_test.py`

**Interfaces:**
- Consumes: `OBJECT_FIELDS["lastmjd"]`, `resolve_field(..., required=False)` from Task 1.

- [ ] **Step 1: Write the failing test**

`test_stale_objects_skip_detection_call`: a `StubAlerce` subclass adds a
page-1 object `{"oid": 1999, "meanra": 1.0, "meandec": 1.0, "lastmjd": 61352.0}`
(before `SINCE` = MJD 61353.0) and has no detections for it.
Assert after `poll(SINCE, 30.0)`: no `query_detections` call with
`oid == 1999` in `stub.calls`; result equals `_expected(30.0)`; no
WARNING mentions `1999`. Also assert an object with **no** `lastmjd` key
still gets its detections call (the guard only acts on a present value).

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/brokers/alerce_test.py::test_stale_objects_skip_detection_call -q`
Expected: FAIL — `KeyError: '1999'` surfaces as a skip warning, or the
detections call is recorded.

- [ ] **Step 3: Implement** — before the detections call, if the
object's `lastmjd` resolves and `mjd_to_datetime(lastmjd) <= since`,
return "filtered out" (not a schema miss) without calling ALeRCE.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/brokers -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lcftp/brokers/alerce.py tests/brokers/alerce_test.py
git commit -m "feat(alerce): skip objects outside the lastmjd window before fetching detections"
```

### Task 3: Live fixture recorder

**Files:**
- Create: `scripts/record-alerce-fixtures.py`
- Create: `tests/scripts/record_alerce_fixtures_test.py`
- Delete: `docs/superpowers/notes/alerce_spike.py`

**Interfaces:**
- Consumes: `AlerceClient` protocol and `datetime_to_mjd` from
  `lcftp.brokers.alerce` (add `query_object` to the protocol).
- Produces (files in `out_dir`, same shapes the existing `StubAlerce`
  reads, so Task 5 can reuse it):
  - `meta.json`: `{"recorded_at": ISO-8601 UTC, "alerce_version": str, "window": [start_mjd, end_mjd], "page_size": int, "pages": int}`
  - `objects_page{N}.json`: `{"items": [...]}` for N = 1..pages, exactly
    what `query_objects(survey="lsst", format="json", lastmjd=window, order_by="lastmjd", order_mode="DESC", page=N, page_size=page_size)` returned.
  - `detections.json`: `{"detections": {str(oid): [...]}}` for every
    recorded object, from `query_detections(survey="lsst", oid=oid, format="json")`.
  - `object.json`: `query_object(survey="lsst", oid=<first oid>, format="json")`
    (with survey extras), to check `oid` against `diaObjectId`.
- Function: `record(client: AlerceClient, out_dir: Path, *, now_mjd: float, window_days: float = 1.0, page_size: int = 10, pages: int = 2) -> dict[str, Any]` returning the meta dict.
- CLI: `uv run python scripts/record-alerce-fixtures.py [--out tests/data/alerce/recorded] [--pages 2] [--page-size 10] [--window-days 1.0]`; builds `alerce.core.Alerce()` and calls `record`.

- [ ] **Step 1: Write the failing test**

Load the script with `importlib.util.spec_from_file_location`. With a
stub client returning two objects on page 1 and none on page 2, call
`record(stub, tmp_path, now_mjd=61354.0)` and assert: `meta.json`
window is `[61353.0, 61354.0]`; `objects_page1.json["items"]` equals the
stub's objects; `objects_page2.json["items"] == []`; `detections.json`
has keys for both oids as strings; `object.json` exists; every recorded
call passed `survey="lsst"`; recording stops paging at the first empty
page. All files are pretty-printed JSON (`indent=1`, `sort_keys=True`)
so diffs of re-recordings are readable.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/scripts/record_alerce_fixtures_test.py -q`
Expected: FAIL — script file not found.

- [ ] **Step 3: Implement** the script, and delete
`docs/superpowers/notes/alerce_spike.py`; update the note's "Status"
line to point at the recorder instead.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/scripts tests/brokers -q && uv run nox -s lint typing`
Expected: PASS. (If `nox` checks `scripts/` with `INP001` or similar,
add the script to the existing per-file ignores in `pyproject.toml`
rather than suppressing inline.)

- [ ] **Step 5: Commit**

```bash
git add scripts/record-alerce-fixtures.py tests/scripts/ docs/superpowers/notes/ pyproject.toml
git commit -m "feat(scripts): record live ALeRCE LSST responses as test fixtures"
```

### Task 4: Record live fixtures (needs a person with internet access)

**This task cannot run in the cloud sandbox.** An executor there stops
after Task 3 and hands this task to a person with internet access, along with the commands below.

- [ ] **Step 1: Record**, on a machine that can reach
`https://api-lsst.alerce.online/`, during or just after an observing night:

```bash
uv run python scripts/record-alerce-fixtures.py --out tests/data/alerce/recorded
```

Expected: prints the meta dict; `tests/data/alerce/recorded/` holds
`meta.json`, `objects_page1.json`, `objects_page2.json`,
`detections.json`, `object.json`, and `objects_page1.json` is non-empty.
If page 1 is empty, widen `--window-days` and re-run.

- [ ] **Step 2: Check size** — `du -sh tests/data/alerce/recorded`.
Expected: under 2 MB. If larger, re-run with `--page-size 5`.

- [ ] **Step 3: Commit and push**

```bash
git add tests/data/alerce/recorded
git commit -m "test(alerce): add recorded live LSST responses"
git push
```

### Task 5: Replay tests, field-table corrections, and spike note

**Files:**
- Create: `tests/brokers/alerce_recorded_test.py`
- Create: `tests/data/alerce/recorded/expected.json`
- Modify: `src/lcftp/brokers/alerce.py` (only the field tables, and only
  as the recording requires)
- Modify: `docs/superpowers/notes/alerce-lsst-spike.md`

**Interfaces:**
- Consumes: `OBJECT_FIELDS`, `DETECTION_FIELDS`, `resolve_field`,
  `MissingFieldError` (Task 1); the recorded files (Tasks 3–4); the
  `StubAlerce` replay pattern from `tests/brokers/alerce_test.py`
  (parameterize its data directory rather than copy it).

- [ ] **Step 1: Write `expected.json` by hand from the recording.**
Pick the first object in `objects_page1.json` whose latest detection
(greatest time field) has a positive PSF flux. Copy, by reading the
JSON, not by running the plugin: `oid`, the latest detection's ra, dec,
band name, alert id, time (MJD) and flux. Compute
`mag = 31.4 − 2.5·log10(flux)` by hand. Save as
`{"oid": ..., "ra": ..., "dec": ..., "band": ..., "alert_id": ..., "mjd": ..., "mag": ...}`.

- [ ] **Step 2: Write the failing tests** in `tests/brokers/alerce_recorded_test.py`

- `test_required_fields_resolve_on_every_recorded_record`: for every
  recorded object, `resolve_field` succeeds for `oid`; for every
  recorded detection, it succeeds for `mjd`, `flux`, `band`; a position
  resolves from the detection or the object. On failure the
  `MissingFieldError` message (field + keys) is the assertion message.
- `test_recorded_golden_candidate`: replay with
  `since = mjd_to_datetime(meta["window"][0])`, `mag_limit = 40.0`;
  the candidate for `expected["oid"]` has exactly the `expected.json`
  values (`mag` with `pytest.approx(abs=1e-6)`,
  `latest_alert_time == mjd_to_datetime(expected["mjd"])`).
- `test_recorded_replay_passes_broker_contract`: `assert_broker_contract`
  over the whole recording with the same `since`, `mag_limit = 40.0`,
  returns at least one candidate and raises nothing.
- `test_recorded_objects_fall_in_window`: every recorded object's
  `lastmjd` lies in `meta["window"]`. This checks that ALeRCE filters
  server-side (spike finding 3).
- `test_oid_matches_dia_object_id`: if `object.json` carries a
  diaObjectId-like key (any key whose lower-case form is `diaobjectid`),
  it equals `int(oid)`.

- [ ] **Step 3: Run to see what fails**

Run: `uv run pytest tests/brokers/alerce_recorded_test.py -q`
Expected: some failures, if the synthetic field names were wrong;
each `MissingFieldError` names the logical field and the real keys.

- [ ] **Step 4: Correct the field tables.** For each miss, put the live
name *first* in that logical field's tuple and keep the old names as
fallbacks. Change nothing else in the plugin. Special cases, each a
ledger ruling:
- `test_recorded_objects_fall_in_window` fails: the server is ignoring
  the window. Task 2's guard already covers that. Change the test to
  assert the guard dropped the out-of-window objects with no detections
  call, and record the finding in the spike note.
- `object.json` has no diaObjectId-like key: the identity stays
  unverified. The test is vacuous by design; say so in the spike note.
- PSF flux is not in nJy, for example if only magnitudes are provided:
  stop. That changes the photometry contract and needs a decision.

- [ ] **Step 5: Run everything**

Run: `uv run pytest tests/brokers tests/scripts -q && uv run nox -s lint typing`
Expected: PASS.

- [ ] **Step 6: Update the spike note.** Change **Status** to "live
recording `<recorded_at>`". Correct findings 3–5 from the recording
(server-side window, real detection field names, `oid` versus
`diaObjectId`). Note the object count per window and the detections call
count per page, so the "Consequence" section about stream brokers rests
on real numbers. Mark `tests/data/alerce/*.json` (outside `recorded/`)
as synthetic edge-case fixtures that are kept on purpose.

- [ ] **Step 7: Commit**

```bash
git add src/lcftp/brokers/alerce.py tests/brokers/alerce_recorded_test.py tests/data/alerce/recorded/expected.json docs/superpowers/notes/alerce-lsst-spike.md
git commit -m "fix(alerce): match field names to recorded live LSST responses"
```

---

## Not in this plan

- **Populating `Candidate.classification` / `score`** from ALeRCE
  probabilities. The DB columns exist and nothing fills them. If the
  recorded object list already carries class and probability, this is
  cheap; if it does not, it costs one more call per object. Decide once
  the recording shows what is there.
- **Stopping paging early** using `order_by=lastmjd DESC`. This only
  pays off once Task 5 confirms the server orders and filters.
