# Plan Export and Frame Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let observers export an lcftp plan as a CSV that NINA can import and a person can read, then ingest their FITS frames back into the spool with full provenance.

**Architecture:** This adds new client modules (`tags`, `export`, `provenance`, `ingest/`) and two CLI commands (`export`, `ingest`). Drivers and the runner are not touched, except that the runner's frame-commit code moves into a shared helper. Ingest bookkeeping lives in the spool's `state.json`, under a new `"ingest"` key.

**Tech Stack:** Python 3.13, click, pydantic, astropy (FITS, SkyCoord, Time), stdlib `csv`, pytest (asyncio auto mode).

**Spec:** `docs/superpowers/specs/2026-11-23-plan-export-and-ingest-design.md`

## Global Constraints

- No changes to `lcftp_models` wire contracts or the server.
- The `TelescopeDriver` protocol is unchanged.
- Target tag: `lcftp-<plan8>-<item_id>`, where `plan8` = `plan_id[:8]`. Allowed characters are `[a-z0-9-]` only.
- The CSV's first five headers are exactly `Familiar Name`, `Catalogue Entry`, `Right Ascension (J2000)`, `Declination (J2000)`, `Position Angle (East)`. Never emit a bare `Right Ascension` header.
- Ingest copies source frames into the spool. It never moves, renames or modifies the originals.
- Ingest never guesses: a frame with no resolvable tag (and no `--item`) is reported and left alone.
- Ingested frames are recorded with `driver="ingest"` and `driver_version=lcftp_client.__version__`.
- Outcomes are recorded only by `--final`.
- Tests: `uv run pytest client/tests -q`. Lint and types: `uv run pre-commit run --all-files`.

## Review Focus

1. A declination between 0 and −1° must keep its sign (`-00d 30m 00.0s`), because NINA's sign detection is "contains `-`". Test in Task 3.
2. When seconds round up to 60, the carry goes into minutes and hours (`23h 59m 59.999s` → `00h 00m 00.00s`, not `60.00s`). Test in Task 3.
3. If the filename tag and the `OBJECT` tag disagree, the frame is rejected as conflicting, not silently resolved. Test in Task 6.
4. The same file ingested twice under a different name or directory is skipped by hash. Test in Task 6.
5. `DATE-OBS` without a timezone is treated as UTC. A frame with `EXPTIME` of 0 or missing is rejected as bad headers, not a crash. Test in Task 5.

---

### Task 1: Target tags

**Files:**
- Create: `client/src/lcftp_client/tags.py`
- Test: `client/tests/tags_test.py`

**Interfaces:**
- Produces:
  - `target_tag(plan_id: str, item_id: str) -> str`
  - `class TagRef(NamedTuple): plan8: str; item_id: str`
  - `find_tags(text: str) -> list[TagRef]`, returning every distinct tag in `text` in order of appearance. The match is case-insensitive and results are lower-cased.

- [ ] **Step 1: Write failing tests**

```python
def test_target_tag() -> None:
    assert target_tag("3f9a1c2e" + "0" * 24, "007") == "lcftp-3f9a1c2e-007"


def test_find_tags_in_nina_filename() -> None:
    name = "2026-11-24_LCFTP-3F9A1C2E-007_LIGHT_r_60.00s_0003.fits"
    assert find_tags(name) == [TagRef("3f9a1c2e", "007")]


def test_find_tags_none_and_multiple() -> None:
    assert find_tags("M42_LIGHT.fits") == []
    assert find_tags(
        "lcftp-aaaaaaaa-001 lcftp-bbbbbbbb-002 lcftp-aaaaaaaa-001"
    ) == [TagRef("aaaaaaaa", "001"), TagRef("bbbbbbbb", "002")]


def test_find_tags_rejects_short_prefix() -> None:
    assert find_tags("lcftp-3f9a1c2-007") == []
```

- [ ] **Step 2: Run** `uv run pytest client/tests/tags_test.py -q`. Expected: FAIL (ImportError).
- [ ] **Step 3: Implement** with regex `lcftp-([0-9a-f]{8})-([0-9]+)` (`re.IGNORECASE`), with word boundaries so that `[0-9a-f]{9}` doesn't match.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(client): target tags tying frames to plan items`

---

### Task 2: Spool plan archive and per-plan outcomes

**Files:**
- Modify: `client/src/lcftp_client/spool.py` (layout docstring, `__init__`, `save_plan`, new methods)
- Test: `client/tests/spool_test.py`

**Interfaces:**
- Produces:
  - `Spool.plans_dir: Path` (`root / "plans"`)
  - `Spool.save_plan(plan)` also writes `plans/<plan_id>.json`
  - `Spool.archive_plan(plan: ObservationPlan) -> None`, which writes the archive only and leaves the current plan alone
  - `Spool.find_plan(plan8: str) -> ObservationPlan`, which raises `LookupError` with a message naming the prefix when no archived plan matches, or when several do (the ambiguous case)
  - `Spool.outcomes_for(plan_id: str) -> dict[str, ItemOutcome]`

- [ ] **Step 1: Write failing tests**
  - `test_save_plan_archives`: `save_plan(make_plan(plan_id="aaaaaaaa1"))`, then `save_plan(make_plan(plan_id="bbbbbbbb1"))`; `find_plan("aaaaaaaa").plan_id == "aaaaaaaa1"`, and `current_plan().plan_id == "bbbbbbbb1"`.
  - `test_archive_plan_keeps_current`: after `archive_plan(other)`, `current_plan()` is unchanged.
  - `test_find_plan_unknown_and_ambiguous`: `pytest.raises(LookupError, match="cccccccc")` when nothing matches; with `aaaaaaaa1` and `aaaaaaaa2` archived, `find_plan("aaaaaaaa")` raises `LookupError(match="ambiguous")`.
  - `test_outcomes_for`: record outcomes for two plans; `outcomes_for("p2")` returns only p2's, keyed by item id.
- [ ] **Step 2: Run** `uv run pytest client/tests/spool_test.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** Refactor `outcomes()` to `return self.outcomes_for(plan.plan_id)`.
- [ ] **Step 4: Run** the client test suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat(client): archive every plan in the spool`

---

### Task 3: CSV export, CLI command and observer guide

**Files:**
- Create: `client/src/lcftp_client/export.py`, `docs/observers.md`
- Modify: `client/src/lcftp_client/cli.py` (new `export` command), `client/README.md` (one-line pointer to `docs/observers.md`)
- Test: `client/tests/export_test.py`, `client/tests/cli_test.py`

**Interfaces:**
- Consumes: `target_tag` (Task 1); `Spool.archive_plan`, `Spool.current_plan` (Task 2)
- Produces:
  - `CSV_COLUMNS: tuple[str, ...]`, the 19 headers in the spec's table order, verbatim
  - `format_ra(ra_deg: float) -> str`, giving `HHh MMm SS.SSs`
  - `format_dec(dec_deg: float) -> str`, giving `±DDd MMm SS.Ss` (the sign is always present)
  - `write_plan_csv(plan: ObservationPlan, out: TextIO) -> None`
  - CLI: `lcftp export [PATH] [--plan-file FILE]`

- [ ] **Step 1: Write failing tests (`export_test.py`)**

```python
def test_format_ra() -> None:
    assert format_ra(83.82208) == "05h 35m 17.30s"
    assert format_ra(359.9999999) == "00h 00m 00.00s"  # carry wraps


def test_format_dec() -> None:
    assert format_dec(-5.39111) == "-05d 23m 28.0s"
    assert format_dec(-0.5) == "-00d 30m 00.0s"  # sign kept
    assert format_dec(10.0) == "+10d 00m 00.0s"
    assert format_dec(29.9999999) == "+30d 00m 00.0s"  # carry


def test_write_plan_csv(plan) -> None:  # conftest make_plan(), plan_id "plan1"
    buf = io.StringIO()
    write_plan_csv(plan, buf)
    rows = list(csv.DictReader(io.StringIO(buf.getvalue())))
    assert list(rows[0]) == list(CSV_COLUMNS)
    assert len(rows) == 3
    r = rows[1]
    assert r["Familiar Name"] == "lcftp-plan1-001"
    assert r["Catalogue Entry"] == "diaObject 101"
    assert r["Right Ascension (J2000)"] == "02h 04m 00.00s"
    assert r["Declination (J2000)"] == "+10d 00m 00.0s"
    assert r["Position Angle (East)"] == "0"
    assert r["Not Before (UTC)"] == "2026-11-10T03:10:00Z"
    assert r["RA (deg)"] == "31.000000"
    assert (
        r["Filter"] == "r"
        and r["Total Exposure (s)"] == "300"
        and r["Target SNR"] == ""
    )
    assert (
        r["Plan ID"] == "plan1"
        and r["Item ID"] == "001"
        and r["Priority"] == "1"
    )
    assert '"' not in buf.getvalue()  # no field needs quoting
```

  Note: `make_plan`'s `plan_id="plan1"` is shorter than 8 characters, so `target_tag` simply uses `plan_id[:8]`. That gives `lcftp-plan1-001`, which is acceptable in tests. Real plan ids are 32 hex characters.

- [ ] **Step 2: Write failing CLI tests (`cli_test.py`)**
  - `test_export_current_plan`: after `init` and `plan` (same setup as `test_full_cycle`), `_invoke(config, "export", str(out))` writes a CSV whose first line starts with `Familiar Name,Catalogue Entry,`.
  - `test_export_to_stdout`: `export` with no path prints the CSV.
  - `test_export_no_plan`: on a fresh spool, `export` exits non-zero and the output contains `lcftp plan`.
  - `test_export_plan_file_archives`: `--plan-file p.json` exports that plan, and afterwards `Spool(spool).find_plan(...)` finds it.
- [ ] **Step 3: Run** `uv run pytest client/tests/export_test.py client/tests/cli_test.py -q`. Expected: FAIL.
- [ ] **Step 4: Implement.** Round to the output precision first (hundredths of a second of RA, tenths of an arcsecond of Dec) and carry from there. Write `Z` timestamps with `dt.strftime("%Y-%m-%dT%H:%M:%SZ")`. Format numbers with `f"{x:g}"`, except RA/Dec (`.6f`) and `Mag` (`.2f`). Write `None` as an empty string. Use `csv.writer(out, lineterminator="\n")`. The CLI writes with `newline=""`.
- [ ] **Step 5: Write `docs/observers.md`**, covering:
  - getting a plan (`lcftp plan --start … --end …`), then `lcftp export tonight.csv`;
  - a column glossary;
  - NINA: Simple Sequencer → *Import targets* → choose the CSV. Exposures come from the user's default template; use the `Filter` and `Total Exposure (s)` columns as a guide. The Target Scheduler plugin also accepts the file (Telescopius format);
  - NINA *Options → Imaging → Image file pattern* must include `$$TARGETNAME$$`;
  - other software: put the tag in the filename or the FITS `OBJECT` header;
  - honour the UTC windows;
  - a placeholder section "Sending frames back", filled in by Task 7.
- [ ] **Step 6: Run** the full client suite and pre-commit. Expected: PASS.
- [ ] **Step 7: Commit** `feat(client): export plans as NINA-importable CSV`

---

### Task 4: Shared frame-commit helper

**Files:**
- Create: `client/src/lcftp_client/provenance.py`
- Modify: `client/src/lcftp_client/runner.py` (`_commit` delegates to the helper)
- Test: `client/tests/provenance_test.py`; the existing `runner_test.py` must still pass unchanged

**Interfaces:**
- Produces:

```python
def commit_frame(
    spool: Spool,
    path: Path,
    *,
    frame_id: UUID,
    plan: ObservationPlan,
    item: PlanItem,
    observatory_id: str,
    driver: str,
    driver_version: str,
    obs_start: datetime,
    exposure_s: float,
    filter: str | None,
    ra: float,
    dec: float,
    driver_metadata: Mapping[str, JsonValue],
) -> FrameRecord: ...
```

  The helper stamps provenance on `path` (which must already be `spool.new_frame_path(frame_id)`), computes the size and checksums after stamping, commits the sidecar, and returns the record.

- [ ] **Step 1: Write failing test** `test_commit_frame_stamps_and_commits`. Write a 4×4 FITS file to `spool.new_frame_path(fid)`, call `commit_frame(...)`, and assert:
  - the header has `LCFTPITM == "001"` and `LCFTPDRV == "ingest/1.0"`;
  - `spool.pending_frames() == [record]`;
  - `record.checksums == compute_checksums(path)`.
- [ ] **Step 2: Run** it. Expected: FAIL.
- [ ] **Step 3: Implement** by moving the body of `Runner._commit`.
- [ ] **Step 4: Run** `uv run pytest client/tests -q`. Expected: PASS, including `runner_test.py`.
- [ ] **Step 5: Commit** `refactor(client): share frame commit between runner and ingest`

---

### Task 5: FITS header reading

**Files:**
- Create: `client/src/lcftp_client/ingest/__init__.py`, `client/src/lcftp_client/ingest/headers.py`
- Test: `client/tests/ingest/headers_test.py`, including a `write_fits(path, **cards)` helper in `client/tests/ingest/conftest.py` that Task 6 reuses

**Interfaces:**
- Produces:
  - `class HeaderError(ValueError)`
  - `class FrameInfo(BaseModel)` (frozen) with fields:
    - `obs_start: AwareDatetime`, `exposure_s: float` (> 0), `filter: str | None`
    - `ra: float | None`, `dec: float | None` (degrees)
    - `object: str | None`
    - `lcftp_stamped: bool` (true when `LCFTPPLN` is present)
    - `metadata: dict[str, JsonValue]`, holding `TELESCOP`/`INSTRUME`/`OBSERVER` when present
  - `read_frame_info(path: Path) -> FrameInfo`, which raises `HeaderError` with a human-readable reason

- [ ] **Step 1: Write failing tests**
  - `test_basic`: given `DATE-OBS="2026-11-24T03:15:00.123"`, `EXPTIME=60.0`, `FILTER="r"`, `RA=31.0`, `DEC=10.0`, `OBJECT="lcftp-plan1-001"`, the result has `obs_start == datetime(2026,11,24,3,15,0,123000,tzinfo=UTC)`, and the other values are as given.
  - `test_exposure_fallback`: `EXPOSURE=30` with no `EXPTIME` gives `exposure_s == 30`.
  - `test_objctra_objctdec`: `OBJCTRA="02 04 00.0"`, `OBJCTDEC="+10 00 00"` gives `ra == pytest.approx(31.0)` and `dec == pytest.approx(10.0)`.
  - `test_missing_pointing`: with no RA keywords, `ra is None`.
  - `test_bad_headers`: a missing `DATE-OBS`, `EXPTIME=0`, or no exposure keyword each raise `HeaderError`.
  - `test_lcftp_stamped`: `LCFTPPLN="x"` gives `lcftp_stamped`.
  - `test_metadata`: `TELESCOP="C8"` gives `metadata == {"TELESCOP": "C8"}`.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement.** Parse `DATE-OBS` with `astropy.time.Time(value, scale="utc").to_datetime(timezone=UTC)`. Parse sexagesimal pointing with `SkyCoord(ra, dec, unit=(u.hourangle, u.deg))`. Also catch `OSError` from `fits.open` and raise it as `HeaderError("not a readable FITS file")`.
- [ ] **Step 4: Run** them. Expected: PASS.
- [ ] **Step 5: Commit** `feat(client): read observer FITS headers for ingest`

---

### Task 6: Ingest engine

**Files:**
- Create: `client/src/lcftp_client/ingest/ingester.py`
- Modify: `client/src/lcftp_client/spool.py` (ingest bookkeeping)
- Test: `client/tests/ingest/ingester_test.py`, `client/tests/spool_test.py`

**Interfaces:**
- Consumes: `find_tags`/`TagRef` (Task 1); `Spool.find_plan` (Task 2); `commit_frame` (Task 4); `read_frame_info`, `HeaderError` (Task 5)
- Produces, on `Spool` (persisted in `state.json["ingest"]`):
  - `ingested_hashes() -> set[str]`
  - `record_ingested(sha256: str, plan_id: str, item_id: str, start: datetime, end: datetime) -> None`
  - `ingested_spans(plan_id: str) -> dict[str, tuple[datetime, datetime]]`, giving the earliest start and latest end per item
  - `unfinalised_plans() -> list[str]`, listing plan ids that have ingested frames and are not finalised, sorted
  - `mark_finalised(plan_id: str) -> None`
- Produces, in `ingester.py`:
  - `class FrameStatus(StrEnum)`, with values: `ingested`, `already_ingested`, `already_lcftp`, `no_tag`, `conflicting_tags`, `unknown_plan`, `unknown_item`, `wrong_observatory`, `bad_headers`, `outside_window`, `off_target`
  - `class FrameResult(BaseModel)`: `path: Path`, `status: FrameStatus`, `tag: str | None`, `detail: str | None`, `warning: str | None`
  - `class IngestReport(BaseModel)`: `results: list[FrameResult]`; `counts() -> dict[str, int]`
  - `ingest_dir(spool: Spool, profile: ObservatoryProfile, directory: Path, *, item_tag: str | None = None, force: bool = False, dry_run: bool = False) -> IngestReport`

Per-frame algorithm. Results are in sorted path order. Files are matched by `*.fits|*.fit|*.fts`, case-insensitively and recursively.

1. Hash the source with SHA-256. If the hash is in `ingested_hashes()`, the status is `already_ingested`.
2. Run `read_frame_info`. On `HeaderError`, the status is `bad_headers`. If `lcftp_stamped`, the status is `already_lcftp`.
3. Collect tags from `find_tags(path.name)` and from `find_tags(info.object or "")`. More than one distinct tag gives `conflicting_tags`. Exactly one tag means use it. None means use `find_tags(item_tag)` if `item_tag` was given; otherwise the status is `no_tag`.
4. Run `find_plan(tag.plan8)`. A `LookupError` gives `unknown_plan`, with the error text as the detail. An `item_id` not in the plan gives `unknown_item`. If `plan.observatory_id != profile.id`, the status is `wrong_observatory`.
5. Unless `force` is set: if `obs_start` is outside `[plan.window.start, plan.window.end]`, the status is `outside_window`. If the pointing is present and its separation from the candidate exceeds `profile.fov_arcmin` arcminutes, the status is `off_target`. If `obs_start` is outside the item's own window, the frame passes with a warning.
6. Unless `dry_run` is set: copy with `shutil.copy2` to `spool.new_frame_path(uuid4())`, then call `commit_frame` with `driver="ingest"` and metadata `{**info.metadata, "source_path": str(path), "source_sha256": h}`, and the pointing taken from the headers or, if absent, from the candidate. Then call `record_ingested(h, plan_id, item_id, obs_start, obs_start + exposure)`. The status is `ingested`. With `dry_run`, the status is still `ingested`, but nothing is written.

- [ ] **Step 1: Write failing spool tests.** `test_ingest_bookkeeping`:
  - record two frames for item `001` and one for `002` of plan `p`;
  - `ingested_spans("p")["001"]` is the min start and max end;
  - `unfinalised_plans() == ["p"]`, and after `mark_finalised("p")` it is `[]`;
  - the hashes persist across a new `Spool(root)`.
- [ ] **Step 2: Write failing ingester tests.** Set up a spool with `profile` (`fov_arcmin` from the conftest) and the archived `make_plan(plan_id="aaaaaaaa" + "0"*24)`. Use `write_fits` with `DATE-OBS` inside item `001`'s window and the item's RA/Dec.
  - `test_ingests_tagged_filename`: `lcftp-aaaaaaaa-001_LIGHT.fits` gives status `ingested`, and there's one pending frame with `item_id == "001"`, `driver == "ingest"` and `driver_metadata["source_path"]` set. The source file's bytes and mtime are unchanged.
  - `test_tag_from_object_header` gives `ingested`.
  - `test_conflicting_tags`: filename `…-001`, `OBJECT` `…-002`, gives `conflicting_tags`, and nothing is pending.
  - `test_duplicate_copy_skipped`: the same file in two subdirectories (one with an `.FIT` suffix) gives `[ingested, already_ingested]`.
  - `test_no_tag` and `test_item_override`: `M42.fits` gives `no_tag`; with `item_tag="lcftp-aaaaaaaa-001"` it gives `ingested`.
  - `test_unknown_plan`, `test_unknown_item`, `test_wrong_observatory` (an archived plan with `observatory_id="other"`), `test_already_lcftp`, and `test_bad_headers` each give their status.
  - `test_outside_window_and_force`: a `DATE-OBS` a day late gives `outside_window`; with `force=True` it gives `ingested`.
  - `test_off_target`: RA offset by 5° gives `off_target`.
  - `test_late_start_warns`: a `DATE-OBS` in item `002`'s window while tagged `001` gives `ingested` with a non-empty `warning`.
  - `test_dry_run_writes_nothing`: everything is `ingested`, but there are no pending frames and `ingested_hashes()` is empty.
- [ ] **Step 3: Run** `uv run pytest client/tests/ingest client/tests/spool_test.py -q`. Expected: FAIL.
- [ ] **Step 4: Implement** the spool methods and `ingest_dir`.
- [ ] **Step 5: Run** the full client suite and pre-commit. Expected: PASS.
- [ ] **Step 6: Commit** `feat(client): ingest observer frames into the spool`

---

### Task 7: Finalise, CLI `ingest`, status, guide

**Files:**
- Create: `client/src/lcftp_client/ingest/finalise.py`
- Modify:
  - `client/src/lcftp_client/cli.py` (new `ingest` command)
  - `client/src/lcftp_client/session.py` (`ClientStatus.unfinalised_plans`)
  - `docs/observers.md` ("Sending frames back")
  - `CHANGELOG.md`
- Test: `client/tests/ingest/finalise_test.py`, `client/tests/cli_test.py`, `client/tests/session_test.py`

**Interfaces:**
- Consumes: `ingest_dir`, `IngestReport` (Task 6); `Spool.ingested_spans`, `mark_finalised`, `unfinalised_plans` (Task 6); `Spool.find_plan`, `outcomes_for` (Task 2)
- Produces:
  - `finalise_plan(spool: Spool, plan: ObservationPlan) -> list[ItemOutcome]`. For each item with no outcome in `outcomes_for(plan.plan_id)`:
    - if it has a span, record `observed` / `OutcomeReason.ok` with `started_at`/`finished_at` taken from the span;
    - otherwise record `skipped` / `OutcomeReason.other` with `detail="no frames ingested"` and `started_at=finished_at=None`.

    Then it calls `mark_finalised`. It returns the outcomes it recorded.
  - `ClientStatus.unfinalised_plans: list[str] = []`
  - CLI: `lcftp ingest [DIR] [--item TAG] [--dry-run] [--force] [--final] [--plan PLAN8]`
    - `--final` requires `--plan`.
    - `DIR` is required unless `--final` is given.
    - Ingest needs a cached profile (`spool.profile()`). Without one, it exits with an error telling the user to run `lcftp plan` first.
    - Output is one line per frame, `<status> <path> [tag] [detail/warning]`, then a totals line.
    - With `--final`, it adds `Finalised <plan_id>: N observed, M skipped`.
    - The exit code is 0 even when frames were rejected; rejections are reported, not fatal.

- [ ] **Step 1: Write failing tests**
  - `test_finalise_records_observed_and_skipped`: ingest frames for item `001` only, then run `finalise_plan`. Item `001` is `observed` with the span's times; `000` and `002` are `skipped` with detail `"no frames ingested"`; `unfinalised_plans() == []`; and `spool.unreported_outcomes()` contains all three.
  - `test_finalise_keeps_existing_outcome`: a runner outcome already recorded for item `000` is left untouched.
  - `test_cli_ingest_then_final`:
    - `ingest DIR` prints `ingested` lines, and `status` JSON shows `unfinalised_plans == [plan_id]`;
    - `ingest --final --plan aaaaaaaa` prints `Finalised`, and `status` shows `[]`.
  - `test_cli_final_requires_plan` and `test_cli_ingest_requires_dir`: both exit non-zero.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Fill in "Sending frames back" in `docs/observers.md`**, covering:
  - `lcftp ingest <dir>` (repeatable), then `lcftp upload`;
  - `--item` for software that can't embed tags;
  - what each rejection status means and when to use `--force`;
  - **finish with `lcftp ingest --final --plan <plan8>`**, explaining that until then the server doesn't know which targets were skipped, and that `lcftp status` lists plans still open.

  Add CHANGELOG entries for export and ingest.
- [ ] **Step 5: Run** `uv run pytest -q` (the whole repo) and `uv run pre-commit run --all-files`. Expected: PASS.
- [ ] **Step 6: Commit** `feat(client): ingest command with --final outcome reporting`
