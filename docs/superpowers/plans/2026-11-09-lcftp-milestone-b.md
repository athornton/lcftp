# LCFTP Milestone B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working proof of concept of LCFTP without telescope hardware:
GitHub-org login, observatory registration, ALeRCE ingest into a candidate
cache, per-observatory observation plans, and a Raspberry Pi client with a
simulated telescope driver that observes offline and uploads checksummed
FITS directly to a bucket.

**Architecture:** A uv workspace with four packages. `lcftp-models`
(the Pydantic contract), `lcftp-auth` (GitHub OAuth + device tokens behind
store protocols), `lcftp` (the FastAPI server, ingest worker and planner;
the workspace root) and `lcftp-client` (the asyncio Pi agent with
entry-point-loaded telescope drivers). Brokers and drivers are plugins.
The server never touches frame bytes: it signs checksum-bound upload URLs
and verifies by metadata.

**Tech Stack:** Python ≥ 3.14; Pydantic v2, pydantic-settings; FastAPI,
uvicorn, Starlette sessions, Jinja2; SQLAlchemy 2 async + asyncpg + Alembic;
httpx; astropy + astroplan; `alerce`; google-cloud-storage, boto3;
google-crc32c; click; pytest, pytest-asyncio, respx, testcontainers,
nbmake; nox + nox-uv; ruff; mypy.

**Spec:** `docs/superpowers/specs/2026-11-09-lcftp-milestone-b-design.md`
(read it before starting any task; section numbers below refer to it).

## Global Constraints

- `requires-python = ">=3.14"` for the root (server) and `auth`;
  `requires-python = ">=3.13"` for `models` and `client` (RSP kernel is
  3.13), with `[tool.ruff] target-version = "py313"` in those two
  packages, so no 3.14-only syntax or stdlib there. CI matrix Python 3.14
  and 3.15 for all, plus a 3.13 job testing `models` and `client`. If a
  third-party dependency has no 3.15 wheel yet, mark the 3.15 job
  `continue-on-error` and note it in the task's commit message; do not
  backport or pin around it.
- Strict typing: `mypy` with the options already in `pyproject.toml`
  plus the pydantic plugin must pass on every package. `Any` only at
  third-party boundaries, narrowed immediately with Pydantic.
- Ruff rules: lander's `ruff-shared.toml` with exactly these changes:
  `target-version = "py314"`; `TCH001`–`TCH003` → `TC001`–`TC003`;
  per-file ignores for `tests/**`, `alembic/**`, `noxfile.py` (spec §3).
- asyncio throughout; blocking third-party calls go through
  `asyncio.to_thread` behind async interfaces.
- Strict TDD: write the failing test, watch it fail, then implement.
- The client package depends only on `lcftp-models` among our packages,
  never on the server, auth or their dependencies.
- No Safir in the MVP.
- Server configuration only from environment variables with prefix
  `LCFTP_` (spec §9, exact names and defaults).
- Object key: `alerts/{dia_object_id}/{observatory_id}/{plan_id}/{frame_id}.fits`.
- FITS provenance keywords: `LCFTPPLN`, `LCFTPITM`, `LCFTPOBS`,
  `LCFTPDIA`, `LCFTPALR`, `LCFTPDRV`.
- Entry-point groups: `lcftp.brokers`, `lcftp.drivers`.
- Flux→AB magnitude: `m = 31.4 − 2.5·log10(flux_nJy)`.
- Defaults: ingest interval 5 min; ingest magnitude margin 0.5;
  candidate max age 7 days; per-target observatory cap 3;
  availability window max 24 h.
- Tests never touch the public network (astropy IERS auto-download off;
  GitHub and ALeRCE mocked). Docker is required for PostgreSQL, MinIO and
  fake-gcs-server containers.
- License MIT. Commits end with the session's attribution trailers.

## Review Focus

1. **Availability windows that cross UTC midnight or sit at extreme
   longitudes** (e.g. Mauna Kea, lon −155.47°, window 2026-11-10T05:00Z →
   2026-11-10T15:00Z, and a site at lon +179.9°) must plan normally and
   assign the correct observing night for the per-target cap. Test added
   to Task 12 and Task 13.
2. **A window entirely in daylight, or shorter than one item slot**,
   returns an empty plan with HTTP 200, never an error. Test added to
   Task 13.
3. **Naive (timezone-less) timestamps from a client** are rejected at
   the model boundary with a clear validation error rather than being
   silently treated as local or UTC. Test added to Task 3.
4. **A crash mid-frame leaves a FITS file without a sidecar `FrameRecord`**
   (or a truncated FITS). On restart the frame is never uploaded; the
   item is marked `failed` with reason `incomplete` unless it already has
   a terminal outcome. Test added to Task 18 and Task 20.
5. **The client loses the response to `/complete` after the server has
   recorded frames as received** and repeats the whole upload. The
   server must answer idempotently (frames reported `received`, no new
   URLs) and the client must then clean its spool. Tests added to
   Task 16 and Task 21.

---

## File Structure

```
pyproject.toml                       # workspace root = server package "lcftp"
ruff-shared.toml                     # from lander, adjusted (Task 1)
noxfile.py                           # de-Gafaelfawr'd (Task 1)
Dockerfile                           # Task 23
.github/workflows/ci.yaml            # Task 1
alembic.ini, alembic/                # Task 5
models/
  pyproject.toml
  src/lcftp_models/
    __init__.py
    site.py           # SiteLocation, FilterSpec, ObservingConstraints
    observatory.py    # ObservatoryProfile, ObservatoryCreate
    candidate.py      # BrokerRef, Candidate
    plan.py           # AvailabilityWindow, ExposureHint, PlanItem, ObservationPlan, PlanStatus
    upload.py         # Checksums, FrameRecord, ItemOutcome, UploadManifest, SignedUpload, UploadTicket, UploadResult
    errors.py         # ErrorCode, ErrorResponse, LcftpError
    checksums.py      # compute_checksums()
    schema.py         # export_schema() for the snapshot
  tests/
    schema_snapshot.json
auth/
  pyproject.toml
  src/lcftp_auth/
    __init__.py
    config.py         # AuthConfig
    principals.py     # UserPrincipal, DevicePrincipal
    stores.py         # UserStore, TokenStore protocols; User, DeviceToken
    github.py         # GitHubClient (OAuth exchange, user, org membership)
    tokens.py         # generate_token(), hash_token()
    auth.py           # build_auth() -> Auth (router, require_user, require_device)
  tests/
src/lcftp/
  __init__.py
  config.py           # Settings (pydantic-settings, LCFTP_ prefix)
  main.py             # create_app()
  cli.py              # click: lcftp web | worker | migrate
  photometry.py       # flux_njy_to_ab_mag()
  db/
    schema.py         # SQLAlchemy ORM tables
    session.py        # engine/sessionmaker factories
  stores/
    users.py tokens.py observatories.py candidates.py plans.py uploads.py
  api/
    health.py observatories.py plans.py uploads.py pages.py deps.py errors.py
  templates/          # Jinja2: base, index, observatory_new, observatory_detail
  brokers/
    base.py           # BrokerSource protocol, BrokerFactory
    loader.py         # load_brokers()
    alerce.py         # AlerceBroker
    testing.py        # FakeBroker + broker_contract_tests()
  ingest/
    worker.py         # run_ingest_once(), run_worker()
  planner/
    settings.py       # PlannerSettings
    night.py          # observing_night()
    visibility.py     # observable_mask()
    planner.py        # plan_observations()
  storage/
    base.py           # ObjectStore protocol, ObjectStat
    keys.py           # frame_object_key()
    memory.py gcs.py s3.py factory.py
tests/                                # server tests; tests/support/ helpers
client/
  pyproject.toml
  noxfile.py
  src/lcftp_client/
    __init__.py
    config.py         # ClientConfig, load_config(), save_config()
    clock.py          # Clock protocol, SystemClock, FakeClock
    api.py            # ServerClient, LcftpApiError, LcftpAuthError
    spool.py          # Spool
    fits.py           # stamp_provenance()
    drivers/
      base.py         # TelescopeDriver, ObservationContext, CapturedFrame, DriverResult, DriverError
      loader.py       # load_driver()
      simulated.py    # SimulatedDriver, SimulatedDriverConfig
      testing.py      # driver_contract_tests()
    runner.py         # Runner
    uploader.py       # Uploader
    session.py        # LcftpClient facade (notebook/CLI use)
    cli.py            # click: lcftp-client ...
  notebooks/lcftp-quickstart.ipynb
  tests/
tests/e2e/                            # Tasks 23–24
docs/superpowers/notes/alerce-lsst-spike.md   # Task 2
```

---

## Phase 0 — Tooling

### Task 1: Workspace, lint, typing and CI

**Files:**
- Modify: `pyproject.toml`, `noxfile.py`, `Makefile`, `.pre-commit-config.yaml`
- Replace: `ruff-shared.toml` (from lander, adjusted)
- Create: `models/pyproject.toml`, `models/src/lcftp_models/__init__.py`,
  `auth/pyproject.toml`, `auth/src/lcftp_auth/__init__.py`,
  `client/pyproject.toml`, `client/noxfile.py`,
  `client/src/lcftp_client/__init__.py`, `src/lcftp/__init__.py`,
  `.github/workflows/ci.yaml`, `tests/smoke_test.py`,
  `models/tests/smoke_test.py`, `auth/tests/smoke_test.py`,
  `client/tests/smoke_test.py`
- Delete: `src/lcftp/services/svc.py` (prototype; ideas re-implemented
  test-first in Tasks 9–10)

**Interfaces:**
- Produces: importable packages `lcftp`, `lcftp_models`, `lcftp_auth`,
  `lcftp_client`; nox sessions `lint`, `typing`, `test` that cover all
  four packages.

- [ ] **Step 1: Write the smoke tests** — one per package, each importing
  the package and asserting `__version__` is a non-empty `str`
  (`importlib.metadata.version(...)`).
- [ ] **Step 2: Run them; expect `ModuleNotFoundError`.**
  Run: `uv run pytest tests/smoke_test.py -v`
- [ ] **Step 3: Configure the workspace.**
  - Root `pyproject.toml`: add `[tool.uv.workspace] members = ["models", "auth", "client"]`
    and `[tool.uv.sources]` entries `lcftp-models = { workspace = true }`,
    `lcftp-auth = { workspace = true }`. Root deps for now:
    `lcftp-models`, `lcftp-auth`. Add `[project.scripts] lcftp = "lcftp.cli:main"`
    (Task 5 creates `cli.py`; until then keep the script line commented).
    Fix `[tool.coverage.run] source` to
    `["lcftp", "lcftp_models", "lcftp_auth", "lcftp_client"]`. Remove the
    dataclasses-avroschema `filterwarnings` entry; keep the testcontainers one.
    Add `"pytest-asyncio"`, `"respx"`, `"testcontainers[postgres,minio]"` to `dev`.
  - Member `pyproject.toml`s: setuptools + setuptools_scm with
    `root = ".."`, `requires-python = ">=3.14"`, MIT license; client declares
    `[project.scripts] lcftp-client = "lcftp_client.cli:main"` (commented
    until Task 22).
  - `ruff-shared.toml`: copy from lander and apply the three adjustments in
    Global Constraints; per-file ignores for tests are the ones currently in
    the Gafaelfawr-derived file (`C901, D101, D103, PLR0915, PT012, S101,
    S106, S301, SLF001`), for `alembic/**` `INP001, D103, D400`, for
    `noxfile.py` `T201`.
  - `noxfile.py`: remove Redis, rename every `GAFAELFAWR_*` to `LCFTP_*`,
    database name `lcftp`, docstrings say LCFTP; drop `dump-schema` (no
    `init` command) and the `docs*` sessions (no Sphinx yet); keep
    subdirectory recursion for `client`. `typing` runs
    `mypy src tests models auth client noxfile.py`.
  - `.github/workflows/ci.yaml`: jobs `lint` (pre-commit) and `test`
    (matrix `python: ["3.14", "3.15"]`, `uv` at the version pinned by
    `scripts/update-uv-version.sh`, runs `nox -s typing test`).
- [ ] **Step 4: Run the full gate.**
  Run: `uv sync --all-groups && uv run nox -s lint typing test`
  Expected: all sessions pass; four smoke tests PASS.
- [ ] **Step 5: Commit** — `build: set up uv workspace, lander ruff rules and CI`.

---

## Phase 1 — ALeRCE spike (throwaway code)

### Task 2: Determine the ALeRCE LSST query strategy

Spec §10 risk 1. Output is a decision and recorded fixtures, not product
code. Use live network here only.

**Files:**
- Create: `docs/superpowers/notes/alerce-lsst-spike.md`
- Create: `tests/data/alerce/*.json` (recorded responses, trimmed to ≤ 20
  objects each)

**Interfaces:**
- Produces for Task 10: the decided call sequence, parameter names, the
  JSON field names for diaObjectId, RA/Dec, latest flux/mag, band,
  last-detection time and alert id, and fixture files named
  `objects_page0.json`, `objects_page1.json`, `lightcurve_<oid>.json`
  (as many as the strategy needs).

- [ ] **Step 1:** In a scratch script (not committed), with `alerce`
  installed, call `Alerce().query_objects(survey="lsst", format="json", ...)`
  and inspect the result keys. Try, in order: filtering/ordering by last
  detection time (e.g. `lastmjd`, `order_by`, `order_mode`), magnitude
  filters, `page_size`. Record which arguments the LSST endpoint honours.
- [ ] **Step 2:** Confirm whether the LSST `oid` *is* the Rubin
  `diaObjectId`; if not, find the field that is.
- [ ] **Step 3:** Determine the cheapest way to obtain each object's latest
  magnitude (object summary field vs. `query_lightcurve` / `query_detections`).
  Measure calls per 100 qualifying objects.
- [ ] **Step 4:** Write the note: findings; the chosen poll algorithm as a
  numbered list (inputs `since: datetime`, `mag_limit: float`); rate-limit
  policy (max concurrent calls, sleep between pages); and whether a
  stream-based broker must be prioritised. Save the fixtures.
- [ ] **Step 5: Commit** — `docs: record ALeRCE LSST spike findings and fixtures`.
- [ ] **Step 6: Stop and report** the decision to Adam before Task 10.

---

## Phase 2 — The contract (`lcftp-models`)

### Task 3: Site, observatory, candidate and window models

**Files:**
- Create: `models/src/lcftp_models/site.py`, `observatory.py`,
  `candidate.py`, and `AvailabilityWindow` in `plan.py`
- Modify: `models/pyproject.toml` (deps `pydantic>=2.12`)
- Test: `models/tests/site_test.py`, `observatory_test.py`,
  `candidate_test.py`, `window_test.py`

**Interfaces:**
- Produces (all `pydantic.BaseModel`, `model_config = ConfigDict(frozen=True, extra="forbid")`; datetimes are `pydantic.AwareDatetime`):
  - `SiteLocation(latitude: float [-90,90], longitude: float [-180,180], elevation_m: float, timezone: str)` — `timezone` validated with `zoneinfo.ZoneInfo`.
  - `FilterSpec(name: str, wavelength_nm: float > 0)`
  - `ObservingConstraints(min_altitude_deg: float = 30.0, max_airmass: float = 2.0, min_moon_separation_deg: float = 30.0)`
  - `ObservatoryCreate(name: str (1–100 chars), site: SiteLocation, aperture_m: float > 0, limiting_mag: float (5–30), fov_arcmin: float > 0, filters: list[FilterSpec] = [], driver: str = "simulated", constraints: ObservingConstraints = ObservingConstraints())`
  - `ObservatoryProfile(ObservatoryCreate)` adds `id: str`, `owner: str` (GitHub login).
  - `BrokerRef(broker: str, broker_object_id: str)`
  - `Candidate(dia_object_id: int, sources: list[BrokerRef] (min 1), latest_alert_id: str | None, latest_alert_time: AwareDatetime, ra: float [0,360), dec: float [-90,90], mag: float, band: str, classification: str | None = None, score: float | None = None, first_seen: AwareDatetime, last_updated: AwareDatetime)`
  - `AvailabilityWindow(start: AwareDatetime, end: AwareDatetime)` — validator: `end > start` and `end - start <= timedelta(hours=24)`; both normalised to UTC.

- [ ] **Step 1: Write failing tests.** Cover at least:
  - `SiteLocation(latitude=91, ...)` raises `ValidationError`; `timezone="Mars/Olympus"` raises.
  - `ObservatoryProfile` round-trips through `model_dump_json()` / `model_validate_json()` unchanged.
  - `Candidate(ra=360.0, ...)` raises; `sources=[]` raises.
  - `AvailabilityWindow` with `end == start` raises; with 24 h 1 s raises; with exactly 24 h passes.
  - **Review Focus 3:** `AvailabilityWindow.model_validate({"start": "2026-11-10T05:00:00", "end": "2026-11-10T15:00:00"})` raises `ValidationError` (naive); the same strings with `-07:00` produce `start == datetime(2026, 11, 10, 12, 0, tzinfo=UTC)`.
- [ ] **Step 2: Run; expect FAIL (ImportError).** `uv run pytest models/tests -v`
- [ ] **Step 3: Implement the models** exactly as listed.
- [ ] **Step 4: Run; expect PASS.** Also `uv run nox -s typing`.
- [ ] **Step 5: Commit** — `feat(models): site, observatory, candidate and window models`.

### Task 4: Plan, upload and error models; checksums; schema snapshot

**Files:**
- Modify: `models/src/lcftp_models/plan.py`
- Create: `models/src/lcftp_models/upload.py`, `errors.py`,
  `checksums.py`, `schema.py`, `models/tests/schema_snapshot.json`,
  `scripts/update-schema-snapshot.py`
- Modify: `models/pyproject.toml` (add `google-crc32c`)
- Test: `models/tests/plan_test.py`, `upload_test.py`,
  `checksums_test.py`, `schema_test.py`

**Interfaces:**
- Produces:
  - `ExposureHint(total_exposure_s: float | None = None, target_snr: float | None = None)` — at least one set.
  - `PlanItem(item_id: str, candidate: Candidate, not_before: AwareDatetime, not_after: AwareDatetime, suggested_filter: str | None, exposure_hint: ExposureHint, priority: int)` — `not_after > not_before`.
  - `ObservationPlan(plan_id: str, observatory_id: str, window: AvailabilityWindow, created_at: AwareDatetime, candidates_as_of: AwareDatetime | None, planner_version: str, items: list[PlanItem])` — items sorted by `not_before`, non-overlapping (validator).
  - `PlanStatus(plan: ObservationPlan, superseded_by: str | None)`
  - `OutcomeStatus = Literal["observed", "skipped", "failed"]`; `OutcomeReason` StrEnum: `ok, window_passed, stopped, driver_error, timeout, weather, incomplete, not_visible, other`.
  - `ItemOutcome(plan_id: str, item_id: str, status: OutcomeStatus, reason: OutcomeReason, detail: str | None = None, started_at: AwareDatetime | None, finished_at: AwareDatetime | None)`
  - `Checksums(sha256: str (64 hex), md5_b64: str, crc32c_b64: str)`
  - `FrameRecord(frame_id: UUID, plan_id: str, item_id: str, observatory_id: str, dia_object_id: int, alert_id: str | None, obs_start: AwareDatetime, exposure_s: float > 0, filter: str | None, driver: str, driver_version: str, ra: float, dec: float, size_bytes: int > 0, checksums: Checksums, driver_metadata: dict[str, JsonValue] = {})`
  - `UploadManifest(batch_id: UUID, frames: list[FrameRecord] (max 50), outcomes: list[ItemOutcome] = [])`
  - `SignedUpload(frame_id: UUID, url: str, method: Literal["PUT"], headers: dict[str, str], expires_at: AwareDatetime)`
  - `UploadTicket(batch_id: UUID, uploads: list[SignedUpload], already_received: list[UUID])`
  - `UploadResult(batch_id: UUID, received: list[UUID], missing: list[UUID], mismatched: list[UUID])`
  - `ErrorCode` StrEnum: `not_authenticated, forbidden, not_found, invalid_request, plan_superseded, frame_not_in_plan, frame_too_large, conflict, internal`.
  - `ErrorResponse(code: ErrorCode, message: str, detail: JsonValue | None = None)`
  - `LcftpError(Exception)` with `__init__(self, code: ErrorCode, message: str, status: int, detail: JsonValue | None = None)` — raised by server and auth code, rendered as `ErrorResponse`.
  - `compute_checksums(path: Path, chunk_size: int = 1 << 20) -> Checksums` — single pass, streaming.
  - `export_schema() -> dict[str, JsonValue]` — `{model_name: Model.model_json_schema()}` for every public model, keys sorted.

- [ ] **Step 1: Write failing tests**, including:
  - `ObservationPlan` with overlapping items raises; unsorted raises; empty `items=[]` is valid.
  - `UploadManifest` with 51 frames raises.
  - `compute_checksums` on a file containing `b"hello\n"` gives
    `sha256 == "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"`,
    `md5_b64 == "sZRqySSS0jR8YjW00mERhA=="`,
    `crc32c_b64` equal to `base64.b64encode(google_crc32c.value(b"hello\n").to_bytes(4, "big"))`.
  - `schema_test.py::test_schema_snapshot` — `export_schema()` equals
    `json.loads(Path("models/tests/schema_snapshot.json").read_text())`; on
    mismatch the assertion message says
    `run scripts/update-schema-snapshot.py if this change is intentional`.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement**; write the script that regenerates the
  snapshot (`json.dumps(export_schema(), indent=2, sort_keys=True)`), run it once.
- [ ] **Step 4: Run; expect PASS** (`uv run pytest models/tests -v`, `nox -s typing`).
- [ ] **Step 5: Commit** — `feat(models): plan, upload and error contract with schema snapshot`.

---

## Phase 3 — Server foundation and auth

### Task 5: Settings, database schema, migrations, app factory, health

**Files:**
- Create: `src/lcftp/config.py`, `src/lcftp/db/schema.py`,
  `src/lcftp/db/session.py`, `src/lcftp/main.py`, `src/lcftp/cli.py`,
  `src/lcftp/api/health.py`, `src/lcftp/api/errors.py`, `alembic.ini`,
  `alembic/env.py`, `alembic/versions/0001_initial.py`
- Create: `tests/conftest.py`, `tests/support/db.py`
- Modify: `pyproject.toml` (deps: `fastapi`, `uvicorn[standard]`,
  `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `pydantic-settings`,
  `click`, `jinja2`, `python-multipart`; enable `lcftp` script)
- Test: `tests/config_test.py`, `tests/schema_test.py`, `tests/health_test.py`

**Interfaces:**
- Produces:
  - `Settings(BaseSettings)` with `env_prefix="LCFTP_"` and every field in
    spec §9: `database_url: PostgresDsn`, `github_client_id: str`,
    `github_client_secret: SecretStr`, `github_org: str`,
    `session_secret: SecretStr`, `base_url: HttpUrl`,
    `storage_backend: Literal["gcs", "s3", "memory"] = "gcs"`,
    `storage_bucket: str`, `storage_endpoint_url: HttpUrl | None = None`
    (S3-compatible endpoints such as MinIO), `brokers: list[str] = ["alerce"]`,
    `ingest_interval: timedelta = 5 min`, `ingest_mag_margin: float = 0.5`,
    `candidate_max_age: timedelta = 7 days`, `target_observatory_cap: int = 3`,
    `max_frame_bytes: int = 200 * 1024**2`, `upload_url_ttl: timedelta = 1 h`.
  - ORM tables (spec §4.4 `db`): `users`, `observatories` (profile JSONB +
    indexed `limiting_mag`), `device_tokens` (unique `token_hash`),
    `candidates` (PK `dia_object_id` BIGINT), `candidate_sources`
    (PK `broker, broker_object_id`), `broker_state` (PK `broker`;
    `high_water_mark`, `last_success`, `consecutive_failures`, `next_attempt`),
    `plans` (PK `plan_id`; `observatory_id`, `window_start`, `window_end`,
    `created_at`, `document` JSONB, `superseded_by` nullable),
    `assignments` (`plan_id`, `dia_object_id`, `observatory_id`, `night` DATE),
    `upload_batches`, `frames` (`status` ∈ `pending|received|mismatched`,
    `object_key`, `sha256`, `size_bytes`, `record` JSONB), `outcomes`
    (PK `plan_id, item_id`).
  - `create_engine(settings: Settings) -> AsyncEngine`;
    `create_sessionmaker(engine) -> async_sessionmaker[AsyncSession]`.
  - `create_app(settings: Settings, *, store: ObjectStore | None = None) -> FastAPI`
    — lifespan creates the engine; `store` override is for tests (Task 14
    defines `ObjectStore`; until then accept `object | None`).
  - Exception handler mapping `lcftp_models.errors.LcftpError` → `ErrorResponse` JSON with its `status`.
  - `GET /health` → `{"status": "ok"}`; `GET /health/ready` →
    `{"database": "ok", "candidates_as_of": <datetime|null>}` with 200, or 503 if the DB query fails.
  - `lcftp migrate` (Alembic upgrade head), `lcftp web` (uvicorn on
    `--host/--port`), `lcftp worker` (stub until Task 11).
  - Test fixtures: session-scoped `postgres_url` (testcontainers, asyncpg
    driver), function-scoped `settings`, `app`, `client: httpx.AsyncClient`
    (ASGITransport), `db_session`; schema created via Alembic, truncated between tests.

- [ ] **Step 1: Write failing tests:** settings load from env and reject a
  missing `LCFTP_GITHUB_ORG`; `ingest_interval` default is 300 s; Alembic
  upgrade on an empty DB produces exactly the tables above (inspect via
  SQLAlchemy `inspect`); `/health` 200; `/health/ready` 200 with
  `candidates_as_of: null`; `/health/ready` 503 when `database_url` points
  at a closed port; an endpoint raising `LcftpError(ErrorCode.not_found, "x", 404)`
  returns `{"code": "not_found", "message": "x", "detail": null}`.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.** Generate `0001_initial.py` with
  `nox -s create-migration -- "initial"` then review it by hand.
- [ ] **Step 4: Run; expect PASS** plus `nox -s typing`.
- [ ] **Step 5: Commit** — `feat(server): settings, schema, migrations, app factory and health`.

### Task 6: GitHub OAuth login (`lcftp-auth`)

**Files:**
- Create: `auth/src/lcftp_auth/config.py`, `principals.py`, `stores.py`,
  `github.py`, `auth.py`
- Create: `src/lcftp/stores/users.py`
- Modify: `auth/pyproject.toml` (deps `lcftp-models`, `fastapi`, `httpx`, `itsdangerous`)
- Test: `auth/tests/github_test.py`, `auth/tests/login_test.py`,
  `tests/stores/users_test.py`

**Interfaces:**
- Produces:
  - `AuthConfig(github_client_id: str, github_client_secret: SecretStr, github_org: str, base_url: HttpUrl, session_secret: SecretStr)`
  - `User(id: int, github_login: str, github_id: int)`;
    `class UserStore(Protocol)`: `async def upsert_github_user(self, login: str, github_id: int) -> User`; `async def get(self, user_id: int) -> User | None`.
  - `UserPrincipal(user_id: int, github_login: str)`
  - `GitHubClient(http: httpx.AsyncClient)`:
    `async def exchange_code(self, code: str, config: AuthConfig) -> str` (access token);
    `async def get_user(self, token: str) -> tuple[str, int]` (login, id);
    `async def is_active_member(self, token: str, org: str) -> bool` — `GET https://api.github.com/user/memberships/orgs/{org}`, true iff 200 and `state == "active"`.
  - `build_auth(config: AuthConfig, *, user_store: Callable[..., UserStore], token_store: Callable[..., TokenStore], http: Callable[..., httpx.AsyncClient]) -> Auth`
    where the callables are FastAPI dependencies. `Auth` exposes
    `router: APIRouter` (`GET /auth/login`, `GET /auth/callback`,
    `POST /auth/logout`), `require_user` (dependency → `UserPrincipal`,
    raises `LcftpError(not_authenticated, …, 401)`), and `session_middleware()`
    returning the configured Starlette `SessionMiddleware` kwargs
    (`same_site="lax"`, `https_only` iff base_url scheme is https).
  - Login flow: `/auth/login` stores a random `state` in the session and
    redirects to `https://github.com/login/oauth/authorize?client_id=…&scope=read:org&state=…&redirect_uri={base_url}/auth/callback`;
    `/auth/callback` checks `state`, exchanges the code, rejects non-members
    with 403 `forbidden` and message `You must be a member of the {org} GitHub organization.`,
    upserts the user, sets `session["user_id"]`, redirects to `/`.
  - `SqlUserStore(session: AsyncSession)` implements `UserStore` (server side).

- [ ] **Step 1: Write failing tests** with `respx` mocking GitHub:
  member logs in → 302 to `/`, session cookie set, `require_user`-protected
  test route returns their login; non-member → 403 with the exact message;
  wrong `state` → 400 `invalid_request`; no session → protected route 401;
  logout clears session. `SqlUserStore.upsert_github_user` is idempotent on
  `github_id` and updates a changed login.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement**; wire `build_auth` into `create_app`.
- [ ] **Step 4: Run; expect PASS** plus typing.
- [ ] **Step 5: Commit** — `feat(auth): GitHub org-restricted OAuth login`.

### Task 7: Device tokens

**Files:**
- Create: `auth/src/lcftp_auth/tokens.py`; extend `stores.py`, `auth.py`
- Create: `src/lcftp/stores/tokens.py`
- Test: `auth/tests/tokens_test.py`, `tests/stores/tokens_test.py`

**Interfaces:**
- Produces:
  - `generate_token() -> str` — `"lcftp_" + secrets.token_urlsafe(32)`;
    `hash_token(token: str) -> str` — sha256 hex.
  - `DeviceToken(id: int, observatory_id: str, owner_login: str, label: str, created_at, last_used: datetime | None, revoked_at: datetime | None)`.
  - `class TokenStore(Protocol)`: `create(observatory_id: str, label: str, token_hash: str) -> DeviceToken`; `lookup(token_hash: str) -> DeviceToken | None`; `touch(token_id: int, when: datetime) -> None`; `revoke(token_id: int, when: datetime) -> None`; `list_for(observatory_id: str) -> list[DeviceToken]`.
  - `DevicePrincipal(token_id: int, observatory_id: str, owner_login: str)`.
  - `Auth.require_device` — reads `Authorization: Bearer`, hashes, looks up;
    revoked/unknown → 401 `not_authenticated` with message
    `Device token is invalid or has been revoked.`; updates `last_used`.
  - `SqlTokenStore(session)`.

- [ ] **Step 1: Write failing tests:** two generated tokens differ and start
  with `lcftp_`; `hash_token` is deterministic and ≠ token; valid bearer →
  principal with correct `observatory_id`; revoked → 401 exact message;
  malformed header → 401; `last_used` updates; the raw token is never
  stored (assert no column equals it).
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** — `feat(auth): observatory-scoped device tokens`.

### Task 8: Observatories API and minimal web pages

**Files:**
- Create: `src/lcftp/stores/observatories.py`, `src/lcftp/api/observatories.py`,
  `src/lcftp/api/pages.py`, `src/lcftp/api/deps.py`,
  `src/lcftp/templates/{base,index,observatory_new,observatory_detail}.html`
- Test: `tests/api/observatories_test.py`, `tests/api/pages_test.py`

**Interfaces:**
- Consumes: `require_user`, `TokenStore`, `ObservatoryCreate`/`ObservatoryProfile`.
- Produces:
  - `SqlObservatoryStore(session)`: `create(owner: User, data: ObservatoryCreate) -> ObservatoryProfile` (id = `uuid4().hex`); `get(id) -> ObservatoryProfile | None`; `list_for_owner(user_id) -> list[ObservatoryProfile]`; `update(id, data) -> ObservatoryProfile`; `faintest_limiting_mag() -> float | None`.
  - JSON API (all `require_user`, owner-only else 404 `not_found`):
    `POST /api/observatories` → 201 `ObservatoryProfile`;
    `GET /api/observatories` → list; `GET/PUT /api/observatories/{id}`;
    `POST /api/observatories/{id}/tokens` body `{"label": str}` → 201
    `{"token": str, "token_info": DeviceToken}` (token shown once);
    `GET /api/observatories/{id}/tokens` (no secrets);
    `DELETE /api/observatories/{id}/tokens/{token_id}` → 204.
  - `GET /api/device/whoami` (`require_device`) → `ObservatoryProfile` of the token's observatory (the client uses this in `init`).
  - HTML (Jinja2, no JS): `/` (login link, or the user's observatories),
    `/observatories/new` (form → creates, redirects to detail),
    `/observatories/{id}` (profile, token list, "create token" form that
    shows the new token once, revoke buttons).

- [ ] **Step 1: Write failing tests:** create/list/get/update round-trip;
  another user's observatory → 404; token create returns a token that then
  authenticates `/api/device/whoami`; revoke makes it 401; form POST to
  `/observatories/new` creates and 303-redirects; `faintest_limiting_mag()`
  returns the max across observatories and `None` when empty.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** — `feat(server): observatory registration, tokens API and pages`.

---

## Phase 4 — Brokers and ingest

### Task 9: Broker plugin layer, photometry, fake broker, contract suite

**Files:**
- Create: `src/lcftp/photometry.py`, `src/lcftp/brokers/base.py`,
  `loader.py`, `testing.py`
- Modify: `pyproject.toml` (`[project.entry-points."lcftp.brokers"]` gets
  `alerce` in Task 10; register a test-only `fake` entry point via
  `tests/support` plugin using `importlib.metadata` monkeypatching)
- Test: `tests/photometry_test.py`, `tests/brokers/loader_test.py`,
  `tests/brokers/fake_contract_test.py`

**Interfaces:**
- Produces:
  - `flux_njy_to_ab_mag(flux_njy: float) -> float` — `31.4 - 2.5*log10(flux)`; `ValueError` for flux ≤ 0.
  - `class BrokerSource(Protocol)`: `name: str`; `version: str`;
    `def poll(self, since: datetime, mag_limit: float) -> AsyncIterator[Candidate]`.
    Contract: yields only candidates with `mag <= mag_limit` and
    `latest_alert_time > since`; every candidate has a `BrokerRef` with
    `broker == self.name`.
  - `BrokerFactory = Callable[[Mapping[str, str]], BrokerSource]` (arg is
    broker-specific config, from env `LCFTP_BROKER_<NAME>_<KEY>`).
  - `load_brokers(names: Sequence[str], env: Mapping[str, str]) -> list[BrokerSource]` — unknown name → `ValueError("Unknown broker plugin: {name}")`.
  - `FakeBroker(candidates: Sequence[Candidate], *, name: str = "fake", fail_times: int = 0)` — raises `BrokerUnavailableError` for the first `fail_times` polls.
  - `BrokerUnavailableError(Exception)` in `base.py`.
  - `broker_contract_tests(make_broker: Callable[[], BrokerSource])` — a
    function returning a list of async test callables, plus a pytest helper
    `make_broker_contract_suite(fixture_name)` used as in
    `tests/brokers/fake_contract_test.py`; asserts the contract above.
- [ ] **Step 1: Write failing tests:** `flux_njy_to_ab_mag(3.631e12)` ≈ `0.0` (abs 1e-3);
  `flux_njy_to_ab_mag(1.0) == 31.4`; `flux_njy_to_ab_mag(1e4)` ≈ `21.4`;
  `0` and negative raise; loader resolves `fake`, rejects `nope` with the
  exact message; FakeBroker passes the contract suite.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** — `feat(brokers): pluggable broker layer with contract tests`.

### Task 10: ALeRCE broker plugin

**Files:**
- Create: `src/lcftp/brokers/alerce.py`
- Modify: `pyproject.toml` (dep `alerce`; entry point `alerce = "lcftp.brokers.alerce:create_broker"`)
- Test: `tests/brokers/alerce_test.py` (uses Task 2 fixtures)

**Interfaces:**
- Consumes: Task 2 note (call sequence and field names), Task 9 protocol.
- Produces: `AlerceBroker(client: Alerce | None = None, *, page_size: int = 100, max_pages: int = 50, concurrency: int = 4)`; `create_broker(config: Mapping[str, str]) -> AlerceBroker`. All `alerce` calls run in `asyncio.to_thread`, always with `survey="lsst"`. Records with missing coordinates or non-positive flux are logged at WARNING and skipped. Any `alerce`/network exception becomes `BrokerUnavailableError`.

- [ ] **Step 1: Write failing tests:** the plugin passes
  `broker_contract_tests` with a stub `Alerce` object that replays the
  fixtures; `poll(since, 18.0)` yields exactly the fixture objects whose
  converted magnitude ≤ 18.0 and detection time > since (compute the
  expected set from the fixtures inside the test); a stub that raises
  `requests.ConnectionError` makes `poll` raise `BrokerUnavailableError`;
  a fixture record with `psfFlux = -5` is skipped with a WARNING logged;
  every call to the stub received `survey="lsst"`.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement** the algorithm from the spike note.
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** — `feat(brokers): ALeRCE LSST broker plugin`.

### Task 11: Ingest worker

**Files:**
- Create: `src/lcftp/stores/candidates.py`, `src/lcftp/ingest/worker.py`
- Modify: `src/lcftp/cli.py` (`lcftp worker`), `src/lcftp/api/health.py`
  (`candidates_as_of` from `broker_state`)
- Test: `tests/stores/candidates_test.py`, `tests/ingest/worker_test.py`

**Interfaces:**
- Produces:
  - `SqlCandidateStore(session)`: `upsert(candidate: Candidate) -> None`
    (merge `sources`; keep earliest `first_seen`; newer `latest_alert_time`
    wins for photometry/alert fields); `expire(older_than: datetime) -> int`;
    `query(max_mag: float) -> list[Candidate]`; `candidates_as_of() -> datetime | None`
    (min `last_success` over enabled brokers);
    broker state get/set: `get_state(broker) -> BrokerState`, `save_state(BrokerState)`.
  - `BrokerState(broker: str, high_water_mark: datetime | None, last_success: datetime | None, consecutive_failures: int, next_attempt: datetime | None)`.
  - `IngestReport(per_broker: dict[str, int], skipped: list[str], failed: list[str], expired: int)`.
  - `run_ingest_once(brokers: Sequence[BrokerSource], sessionmaker, settings: Settings, now: datetime) -> IngestReport`:
    mag limit = `faintest_limiting_mag() + settings.ingest_mag_margin`; if no
    observatories, poll nothing (all brokers in `skipped`); per broker, skip
    if `now < next_attempt`; poll from `high_water_mark or now - candidate_max_age`;
    on success advance the high-water mark to the max `latest_alert_time`
    seen (unchanged if none), set `last_success = now`, reset failures; on
    `BrokerUnavailableError` increment failures and set
    `next_attempt = now + min(settings.ingest_interval * 2**failures, 1 h)`.
    Then expire candidates with `last_updated < now - candidate_max_age`.
    One broker failing never prevents the others.
  - `run_worker(settings: Settings) -> None` — loop calling `run_ingest_once` every `ingest_interval`; handles SIGTERM by finishing the current iteration.

- [ ] **Step 1: Write failing tests** (FakeBroker, real Postgres):
  same `dia_object_id` from brokers `fake-a` and `fake-b` → one row with two
  sources; mag limit passed to the broker is `19.5` when the faintest
  observatory is `19.0`; no observatories → broker not polled; a broker
  with `fail_times=2` → after first run `consecutive_failures == 1` and
  `next_attempt == now + 10 min`, a second broker in the same run still
  ingests; after a success failures reset to 0; candidates older than
  7 days are expired; `/health/ready` reports `candidates_as_of` after one run.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** — `feat(ingest): candidate cache worker with per-broker backoff`.

---

## Phase 5 — Planning

### Task 12: Planner (pure)

**Files:**
- Create: `src/lcftp/planner/settings.py`, `night.py`, `visibility.py`, `planner.py`
- Modify: `pyproject.toml` (deps `astropy`, `astroplan`)
- Modify: `tests/conftest.py` (astropy offline: `iers.conf.auto_download = False`, `iers.conf.auto_max_age = None`)
- Test: `tests/planner/night_test.py`, `visibility_test.py`, `planner_test.py`

**Interfaces:**
- Produces:
  - `PlannerSettings(grid_step: timedelta = 5 min, slot: timedelta = 10 min, default_exposure_s: float = 300.0, observatory_cap: int = 3, version: str = "1")`.
  - `observing_night(when: datetime, tz: str) -> date` — local calendar date of `when − 12 h` in the site's timezone.
  - `observable_mask(site: SiteLocation, constraints: ObservingConstraints, coords: SkyCoord, times: Time) -> NDArray[np.bool_]` shape `(n_targets, n_times)` — astroplan `AltitudeConstraint(min_alt)`, `AirmassConstraint(max_airmass)`, `MoonSeparationConstraint(min_moon_sep)`, `AtNightConstraint.twilight_astronomical()`; the site converts via an internal `to_observer(site) -> astroplan.Observer`.
  - `plan_observations(profile: ObservatoryProfile, window: AvailabilityWindow, candidates: Sequence[Candidate], assignment_counts: Mapping[int, int], settings: PlannerSettings) -> list[PlanItem]`.
    Algorithm:
    1. Keep candidates with `mag <= profile.limiting_mag`.
    2. Grid: `window.start` to `window.end` step `grid_step`; compute mask.
    3. Score = `(limiting_mag − mag) − 0.1 × hours(window.start − latest_alert_time)`; if `assignment_counts.get(id, 0) >= cap`, subtract `100`.
    4. Greedy, highest score first: place each candidate in the earliest free run of `ceil(slot/grid_step)` consecutive grid points that are all observable for it and unused; skip if none.
    5. Return items sorted by `not_before`; `item_id = f"{i:03d}"` in that order; `priority` = rank by score (0 = best); `suggested_filter` = the profile filter whose name equals `candidate.band`, else first filter's name, else `None`; `exposure_hint = ExposureHint(total_exposure_s=settings.default_exposure_s)`.

- [ ] **Step 1: Write failing tests** with fixed data (site Tucson
  32.23, −110.95, 728 m, `America/Phoenix`; window 2026-11-10T02:00Z →
  2026-11-10T12:00Z):
  - a target at Dec −80° is never planned; a target at RA of local
    midnight, Dec +20°, mag 12 with limit 15 is planned;
  - a mag-16 target with limit 15 is excluded;
  - every planned item's slot satisfies altitude ≥ 30° and airmass ≤ 2 at
    both ends (recompute with astroplan in the test);
  - items are time-ordered and non-overlapping; `ObservationPlan` validator accepts them;
  - two otherwise-equal targets, one with `assignment_counts == 3` → the
    other gets the earlier/higher priority;
  - a window 2026-11-10T18:00Z → 2026-11-10T22:00Z (daytime in Tucson) → `[]`;
  - `observing_night(datetime(2026,11,10,5,tzinfo=UTC), "America/Phoenix") == date(2026,11,9)`;
  - **Review Focus 1:** site lon −155.47 (`Pacific/Honolulu`) window
    2026-11-10T05:00Z → 15:00Z plans a visible target; site lon +179.9
    (`Pacific/Fiji`) gives `observing_night` consistent with local date;
    neither raises.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.** Vectorise with one `SkyCoord` array.
- [ ] **Step 4: Run; expect PASS**; planner tests must finish in < 10 s.
- [ ] **Step 5: Commit** — `feat(planner): astroplan visibility, ranking and greedy packing`.

### Task 13: Availability → plan API

**Files:**
- Create: `src/lcftp/stores/plans.py`, `src/lcftp/api/plans.py`
- Test: `tests/api/plans_test.py`

**Interfaces:**
- Consumes: `require_device`, `SqlCandidateStore.query`, `plan_observations`, `observing_night`.
- Produces:
  - `SqlPlanStore(session)`: `save(plan: ObservationPlan, night: date) -> None` (also writes `assignments`); `get(plan_id) -> PlanStatus | None`; `supersede_overlapping(observatory_id, window, new_plan_id) -> list[str]`; `assignment_counts(night: date, dia_object_ids: Sequence[int]) -> dict[int, int]` (distinct observatories, excluding superseded plans).
  - `POST /api/observatories/{id}/availability` (`require_device`; token's observatory must equal `{id}` else 403 `forbidden`), body `AvailabilityWindow` → 201 `ObservationPlan`. Builds `plan_id = uuid4().hex`, `created_at = now`, `candidates_as_of` from the store, `planner_version = PlannerSettings.version`.
  - `GET /api/plans/{plan_id}` (`require_device`, own observatory only) → `PlanStatus`.

- [ ] **Step 1: Write failing tests:** seeded candidates → 201 plan whose
  items reference only seeded candidates; a second overlapping request →
  new plan and `GET` of the first shows `superseded_by` = second id, and
  its assignments no longer count; other observatory's token → 403;
  **Review Focus 2:** daytime window → 201 with `items == []`; a window of
  5 minutes → 201 with `items == []`; **Review Focus 1:** a Mauna Kea
  window crossing UTC midnight stores `night == 2026-11-09` in `assignments`;
  naive datetimes in the body → 422.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** — `feat(server): availability-to-plan endpoint with supersession`.

---

## Phase 6 — Storage and uploads

### Task 14: Object store abstraction and in-memory store

**Files:**
- Create: `src/lcftp/storage/base.py`, `keys.py`, `memory.py`
- Test: `tests/storage/keys_test.py`, `tests/storage/memory_test.py`

**Interfaces:**
- Produces:
  - `ObjectStat(size_bytes: int, sha256: str | None, md5_b64: str | None, crc32c_b64: str | None)`.
  - `class ObjectStore(Protocol)`: `async def sign_upload(self, key: str, size_bytes: int, checksums: Checksums, ttl: timedelta) -> SignedUpload`; `async def stat(self, key: str) -> ObjectStat | None`. The returned `SignedUpload.headers` must be sent verbatim by the client and bind the checksum.
  - `frame_object_key(record: FrameRecord) -> str` — the Global Constraints format.
  - `MemoryObjectStore(base_url: str = "http://memory.invalid")` with
    `sign_upload` returning `url = f"{base_url}/{key}"` and
    `headers = {"x-lcftp-sha256": checksums.sha256}`; a
    `put(key: str, data: bytes) -> None` test hook that records actual
    sha256/md5/crc32c; and an ASGI app `MemoryObjectStore.asgi_app` that
    accepts `PUT {base_url}/{key}` and rejects (400) when the body's
    sha256 ≠ the `x-lcftp-sha256` header, so server tests can exercise
    uploads through `httpx.ASGITransport`.
- [ ] **Step 1: Write failing tests:** key format for a sample record equals
  `alerts/123/obs1/plan1/<uuid>.fits`; `stat` of a missing key → `None`;
  PUT via the ASGI app with correct header → stored, `stat` matches;
  wrong body → 400 and not stored.
- [ ] **Step 2: Run; expect FAIL.**  **Step 3: Implement.**  **Step 4: PASS.**
- [ ] **Step 5: Commit** — `feat(storage): object store protocol and in-memory implementation`.

### Task 15: GCS and S3 stores with checksum-bound signed URLs

**Files:**
- Create: `src/lcftp/storage/gcs.py`, `src/lcftp/storage/s3.py`,
  `src/lcftp/storage/factory.py`
- Modify: `pyproject.toml` (deps `google-cloud-storage`, `boto3`)
- Test: `tests/storage/s3_test.py` (MinIO via testcontainers),
  `tests/storage/gcs_test.py` (`fsouza/fake-gcs-server` via testcontainers `DockerContainer`)

**Interfaces:**
- Produces:
  - `S3ObjectStore(bucket: str, endpoint_url: str | None, client: Any = None)` — presigned PUT with `ChecksumSHA256` = base64 sha256 → header `x-amz-checksum-sha256`; also `x-amz-meta-sha256` (hex); `stat` via `head_object(ChecksumMode="ENABLED")`. boto3 calls via `to_thread`.
  - `GcsObjectStore(bucket: str, client: storage.Client | None = None)` — V4 signed PUT URL with required headers `x-goog-hash: crc32c=<b64>,md5=<b64>` and `x-goog-meta-sha256: <hex>`; `stat` via `blob.reload()` returning `crc32c`, `md5_hash`, metadata sha256.
  - `create_object_store(settings: Settings) -> ObjectStore` selecting by `storage_backend`.
- [ ] **Step 1: Write failing integration tests** (marked `@pytest.mark.docker`):
  for each backend, `sign_upload` then `httpx.put(url, content=data, headers=headers)` succeeds and `stat` returns matching size and checksums;
  the same signed URL with **different bytes** fails with a 4xx and `stat` stays `None`.
  If fake-gcs-server does not enforce `x-goog-hash`, keep that GCS
  assertion but mark it `xfail(strict=True, reason="fake-gcs-server does not validate x-goog-hash")` and record this in the commit message.
- [ ] **Step 2: Run; expect FAIL.**  **Step 3: Implement.**  **Step 4: PASS.**
- [ ] **Step 5: Commit** — `feat(storage): GCS and S3 stores with checksum-bound uploads`.

### Task 16: Upload endpoints

**Files:**
- Create: `src/lcftp/stores/uploads.py`, `src/lcftp/api/uploads.py`
- Test: `tests/api/uploads_test.py`

**Interfaces:**
- Consumes: `ObjectStore`, `frame_object_key`, `SqlPlanStore.get`, `require_device`.
- Produces:
  - `POST /api/uploads` body `UploadManifest` → 200 `UploadTicket`. Each
    frame must have `observatory_id` == token's observatory (403
    `forbidden`), reference an existing plan of that observatory and an
    `item_id` in it (422 `frame_not_in_plan`), and `size_bytes <= max_frame_bytes`
    (422 `frame_too_large`). Frames already `received` go in
    `already_received` and get no URL; others are upserted `pending` and
    signed. Outcomes are upserted by `(plan_id, item_id)`. Re-posting the
    same `batch_id` is allowed and re-signs pending frames.
  - `POST /api/uploads/{batch_id}/complete` → 200 `UploadResult`: for each
    frame in the batch, `stat` its key; missing → `missing`; size or
    sha256/crc32c differ → `mismatched` (status `mismatched`); else
    `received` with `received_at`. Idempotent.
- [ ] **Step 1: Write failing tests** (MemoryObjectStore): happy path
  manifest → ticket → `put` → complete → all received; frame for another
  observatory → 403; unknown item → 422 `frame_not_in_plan`; oversize → 422;
  missing object → in `missing`; tampered object → `mismatched`;
  outcomes stored once even when posted twice;
  **Review Focus 5:** after complete, re-POST the same manifest → ticket
  with `uploads == []` and all ids in `already_received`; complete again →
  same `UploadResult`.
- [ ] **Step 2: Run; expect FAIL.**  **Step 3: Implement.**  **Step 4: PASS.**
- [ ] **Step 5: Commit** — `feat(server): upload manifest and completion endpoints`.

---

## Phase 7 — The client

### Task 17: Client config, clock and server API client

**Files:**
- Create: `client/src/lcftp_client/config.py`, `clock.py`, `api.py`
- Modify: `client/pyproject.toml` (deps `lcftp-models`, `httpx`, `click`, `astropy`, `numpy`)
- Test: `client/tests/config_test.py`, `client/tests/api_test.py`

**Interfaces:**
- Produces:
  - `ClientConfig(server_url: HttpUrl, token: SecretStr, observatory_id: str, spool_dir: Path, driver: str = "simulated", driver_config: dict[str, JsonValue] = {}, nightly_start: time | None = None, nightly_end: time | None = None, item_timeout_s: float = 1800.0)`.
  - `default_config_path() -> Path` (`$LCFTP_CLIENT_CONFIG` or `~/.config/lcftp/client.toml`); `load_config(path: Path | None = None) -> ClientConfig`; `save_config(cfg: ClientConfig, path: Path | None = None) -> None` (TOML written by a small serializer, file mode `0o600`).
  - `class Clock(Protocol)`: `def now(self) -> datetime`; `async def sleep_until(self, when: datetime) -> None`. `SystemClock`; `FakeClock(start: datetime)` with `advance(delta)`, where `sleep_until` advances instantly.
  - `ServerClient(base_url: str, token: str, *, http: httpx.AsyncClient | None = None)`: `whoami() -> ObservatoryProfile`; `request_plan(window: AvailabilityWindow) -> ObservationPlan`; `get_plan(plan_id) -> PlanStatus`; `create_upload(manifest: UploadManifest) -> UploadTicket`; `complete_upload(batch_id: UUID) -> UploadResult`; `aclose()`.
  - `LcftpApiError(code: ErrorCode, message: str, status: int)`; `LcftpAuthError(LcftpApiError)` for 401/403. Network errors propagate as `httpx.TransportError`.
- [ ] **Step 1: Write failing tests:** config round-trips through TOML and
  the file mode is `0o600`; `token` is not in `repr(cfg)`; with `respx`,
  each `ServerClient` method sends `Authorization: Bearer <token>` and
  parses the model; a 401 `ErrorResponse` raises `LcftpAuthError` with the
  server's message; a 422 raises `LcftpApiError` with `code`.
- [ ] **Step 2: Run; expect FAIL.**  **Step 3: Implement.**  **Step 4: PASS.**
- [ ] **Step 5: Commit** — `feat(client): configuration, clock and server API client`.

### Task 18: Spool

**Files:**
- Create: `client/src/lcftp_client/spool.py`
- Test: `client/tests/spool_test.py`

**Interfaces:**
- Produces: `Spool(root: Path)` with layout `plan.json`, `state.json`,
  `frames/{frame_id}.fits`, `frames/{frame_id}.json`, `bad/`.
  - `save_plan(plan: ObservationPlan) -> None` (replaces the current plan and resets state only if `plan_id` differs); `current_plan() -> ObservationPlan | None`.
  - `record_outcome(outcome: ItemOutcome) -> None`; `outcomes() -> dict[str, ItemOutcome]` (by item_id); `unreported_outcomes() -> list[ItemOutcome]`; `mark_outcomes_reported(item_ids) -> None`.
  - `new_frame_path(frame_id: UUID) -> Path`; `commit_frame(record: FrameRecord) -> None` (writes sidecar last); `pending_frames() -> list[FrameRecord]` (frames with both files and a valid sidecar); `remove_frame(frame_id) -> None`; `mark_bad(frame_id, reason: str) -> None` (moves both files to `bad/`).
  - `recover() -> list[UUID]` — moves FITS files with no valid sidecar to `bad/` and returns their ids.
  - All JSON writes atomic (`tmp` file in the same dir, `fsync`, `os.replace`).
- [ ] **Step 1: Write failing tests:** state survives a new `Spool` on the
  same root; an exception injected between temp write and replace (monkeypatch
  `os.replace`) leaves the previous `state.json` intact;
  **Review Focus 4:** a `.fits` without sidecar, and one whose sidecar is
  truncated JSON, are both excluded from `pending_frames()` and moved to
  `bad/` by `recover()`; `mark_bad` moves both files.
- [ ] **Step 2: Run; expect FAIL.**  **Step 3: Implement.**  **Step 4: PASS.**
- [ ] **Step 5: Commit** — `feat(client): crash-safe on-disk spool`.

### Task 19: Driver layer and simulated driver

**Files:**
- Create: `client/src/lcftp_client/drivers/base.py`, `loader.py`,
  `simulated.py`, `testing.py`, `client/src/lcftp_client/fits.py`
- Modify: `client/pyproject.toml` (entry point `simulated = "lcftp_client.drivers.simulated:create_driver"`)
- Test: `client/tests/drivers/simulated_test.py`, `loader_test.py`, `client/tests/fits_test.py`

**Interfaces:**
- Produces:
  - `CapturedFrame(frame_id: UUID, path: Path, obs_start: AwareDatetime, exposure_s: float, filter: str | None, ra: float, dec: float, driver_metadata: dict[str, JsonValue])`.
  - `DriverResult(frames: list[CapturedFrame], weather_blocked: bool = False)`.
  - `DriverError(Exception)`.
  - `ObservationContext(plan_id: str, observatory_id: str, fov_arcmin: float, new_frame_path: Callable[[UUID], Path], clock: Clock)`.
  - `class TelescopeDriver(Protocol)`: `name: str`; `version: str`; `async def connect(self) -> None`; `async def disconnect(self) -> None`; `async def observe(self, item: PlanItem, ctx: ObservationContext) -> DriverResult`; `async def park(self) -> None`.
  - `DriverFactory = Callable[[Mapping[str, JsonValue]], TelescopeDriver]`; `load_driver(name: str, config: Mapping[str, JsonValue]) -> TelescopeDriver` (unknown → `ValueError("Unknown telescope driver: {name}")`).
  - `SimulatedDriverConfig(seed: int = 0, failure_rate: float = 0, timeout_rate: float = 0, cloud_rate: float = 0, image_size: int = 256, frames_per_item: int = 1)`. `SimulatedDriver.observe` writes `frames_per_item` FITS files (int16, Gaussian sky + PSF stars, target flux scaled by `10**(-0.4*(mag - 20))`, TAN WCS centred on the target with pixel scale `fov_arcmin*60/image_size` arcsec, headers `DATE-OBS`, `EXPTIME`, `FILTER`, `OBJECT`); failure → `DriverError`; timeout → `await asyncio.sleep(math.inf)`-style hang (cancellable); clouds → `DriverResult(frames=[], weather_blocked=True)`. It never sleeps for the exposure time.
  - `stamp_provenance(path: Path, record: FrameRecord) -> None` — adds `LCFTPPLN`, `LCFTPITM`, `LCFTPOBS`, `LCFTPDIA`, `LCFTPALR` (`""` if none), `LCFTPDRV` (`"{driver}/{driver_version}"`) to the primary header in place.
  - `driver_contract_tests(make_driver)` — observe writes only to paths from `ctx.new_frame_path`, frames are readable FITS with valid WCS, `park` is idempotent.
- [ ] **Step 1: Write failing tests:** simulated driver passes the contract;
  the target pixel at WCS(ra, dec) is brighter than the median by > 5σ for
  mag 12; seed reproducibility (same seed → identical bytes); `failure_rate=1`
  raises `DriverError`; `cloud_rate=1` → `weather_blocked`; `timeout_rate=1`
  observe does not finish within `asyncio.wait_for(..., 0.1)`; `stamp_provenance`
  writes all six keywords; loader rejects `nope` with the exact message.
- [ ] **Step 2: Run; expect FAIL.**  **Step 3: Implement.**  **Step 4: PASS.**
- [ ] **Step 5: Commit** — `feat(client): driver plugins and simulated telescope`.

### Task 20: Runner

**Files:**
- Create: `client/src/lcftp_client/runner.py`
- Test: `client/tests/runner_test.py`

**Interfaces:**
- Consumes: `Spool`, `TelescopeDriver`, `Clock`, `stamp_provenance`, `compute_checksums`.
- Produces: `Runner(driver: TelescopeDriver, spool: Spool, clock: Clock, *, observatory: ObservatoryProfile, item_timeout_s: float)`:
  - `async def run(self, plan: ObservationPlan) -> list[ItemOutcome]` — saves the plan to the spool, calls `spool.recover()`, `driver.connect()`; for each item without a terminal outcome, in order: if stop requested → `skipped/stopped`; if `clock.now() > not_after` → `skipped/window_passed`; else `sleep_until(not_before)` then `asyncio.wait_for(driver.observe(...), item_timeout_s)`. Results: frames → stamp, checksum, `commit_frame`, `observed/ok`; `weather_blocked` → `skipped/weather`; `DriverError` → `failed/driver_error` (detail = str(exc)); timeout → `failed/timeout`. An item with frames already committed but no outcome (crash after commit) → `observed/ok`; an item recovered as bad by `recover()` with no outcome → `failed/incomplete`. Always `park()` and `disconnect()` in `finally`.
  - `def request_stop(self) -> None` (thread- and signal-safe; wakes `sleep_until`).
- [ ] **Step 1: Write failing tests** (FakeClock, SimulatedDriver):
  3-item plan all observed with 3 committed frames and stamped headers;
  item whose `not_after` is past → `window_passed`; driver failure on item 2
  doesn't stop item 3; timeout → `failed/timeout`; `request_stop()` during
  item 2 → item 3 `skipped/stopped` and `park` called exactly once;
  restarting a run on the same spool after outcomes for items 1–2 only
  observes item 3; **Review Focus 4:** a pre-seeded orphan FITS for item 2
  (no sidecar, no outcome) → item 2 `failed/incomplete` and the orphan is in `bad/`.
- [ ] **Step 2: Run; expect FAIL.**  **Step 3: Implement.**  **Step 4: PASS.**
- [ ] **Step 5: Commit** — `feat(client): offline plan runner with stop and resume`.

### Task 21: Uploader

**Files:**
- Create: `client/src/lcftp_client/uploader.py`
- Test: `client/tests/uploader_test.py`

**Interfaces:**
- Consumes: `ServerClient`, `Spool`.
- Creates for tests: `client/tests/support/fake_bucket.py` — `FakeBucket` ASGI app accepting `PUT /{key}`, verifying `x-lcftp-sha256` like `MemoryObjectStore`, with knobs `reject_next: int` (return 403 for the next N PUTs) and a `stored: dict[str, bytes]`. The client test suite must not import the server package.
- Produces: `Uploader(server: ServerClient, spool: Spool, *, http: httpx.AsyncClient | None = None, batch_size: int = 50, max_attempts: int = 5, base_delay_s: float = 1.0, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep)`:
  - `async def flush(self) -> UploadSummary` where `UploadSummary(uploaded: int, already_received: int, bad: int, remaining: int)`.
  - Per batch: `create_upload` → PUT each `SignedUpload` (stream file, send `headers` verbatim) → `complete_upload` → remove `received` and `already_received` frames from the spool and mark outcomes reported. A PUT 4xx (expired URL or checksum rejection) → re-POST the manifest once for fresh URLs and retry that frame; `mismatched` from complete → re-upload once; second mismatch or second 4xx → `spool.mark_bad`. Transport errors/5xx → exponential backoff with full jitter up to `max_attempts`, then stop and leave the rest pending. `LcftpAuthError` → stop immediately, raise to caller, delete nothing.
- [ ] **Step 1: Write failing tests:** happy path empties `frames/`;
  a bucket that returns 403 once then accepts → frame uploaded after one
  re-POST; a corrupted frame → `bad == 1` after two attempts and the file
  in `bad/`; server 401 → `LcftpAuthError` raised and all frames still
  present; transport error 3 times then success → uploaded with 3 sleeps;
  **Review Focus 5:** first `complete_upload` raises `httpx.ReadTimeout`
  after the server recorded receipt, next `flush()` gets `already_received`
  and cleans the spool without uploading again.
- [ ] **Step 2: Run; expect FAIL.**  **Step 3: Implement.**  **Step 4: PASS.**
- [ ] **Step 5: Commit** — `feat(client): resumable checksum-verified uploader`.

### Task 22: Client facade and CLI

**Files:**
- Create: `client/src/lcftp_client/session.py`, `client/src/lcftp_client/cli.py`
- Modify: `client/pyproject.toml` (enable `lcftp-client` script)
- Test: `client/tests/session_test.py`, `client/tests/cli_test.py`

**Interfaces:**
- Produces:
  - `LcftpClient(config: ClientConfig, *, clock: Clock | None = None, http: httpx.AsyncClient | None = None)` — async context manager composing `ServerClient`, `Spool`, driver, `Runner`, `Uploader`: `request_plan(start: datetime, end: datetime) -> ObservationPlan`; `run(plan: ObservationPlan | None = None) -> list[ItemOutcome]` (default: spool's current plan); `upload() -> UploadSummary`; `stop() -> None`; `status() -> ClientStatus(plan_id: str | None, outcomes: dict[str, int], pending_frames: int, bad_frames: int)`; `night_once(start, end)` = plan → run → upload, where planning failure due to `httpx.TransportError` falls back to the spool's current plan if its window overlaps.
  - CLI (click, `asyncio.run`): `init --server URL --token TOKEN [--spool DIR] [--config PATH]` (calls `whoami`, writes config with observatory id and driver from the profile); `plan --start ISO --end ISO`; `run`; `upload`; `daemon` (each night at `nightly_start`–`nightly_end` local: `night_once`, then retry `upload` every 15 min until empty; SIGINT/SIGTERM → `stop`); `stop` (writes a `STOP` file in the spool that the runner polls between items and during sleeps); `status` (prints JSON).
- [ ] **Step 1: Write failing tests:** `init` against a respx-mocked server
  writes a config with the profile's id; `plan`/`run`/`upload` via
  `click.testing.CliRunner` on a mocked server + `FakeBucket` produce
  frames and then an empty spool; `stop` file causes remaining items to be
  `skipped/stopped`; `night_once` with a server that is down uses the
  stored overlapping plan; `status` output parses as `ClientStatus`.
- [ ] **Step 2: Run; expect FAIL.**  **Step 3: Implement.**  **Step 4: PASS.**
- [ ] **Step 5: Commit** — `feat(client): LcftpClient facade and lcftp-client CLI`.

---

## Phase 8 — Integration

### Task 23: Container image and end-to-end test

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `tests/e2e/conftest.py`, `tests/e2e/night_test.py`
- Modify: `noxfile.py` (session `e2e`, run in CI), `.github/workflows/ci.yaml` (job `e2e`, job `docker` building the image)

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `Dockerfile`: multi-stage, `python:3.14-slim`, uv at the pinned
    version, installs the root package (server + models + auth) only,
    non-root user, `ENTRYPOINT ["lcftp"]`, default `CMD ["web"]`.
  - E2E fixtures: Postgres + MinIO containers; uvicorn serving
    `create_app` with `storage_backend="s3"` on a free localhost port in a
    background task; GitHub mocked by seeding a user and observatory
    directly through the stores and minting a token; FakeBroker with
    10 candidates around a Tucson site.
- [ ] **Step 1: Write the failing test** `test_simulated_night`: run
  `run_ingest_once`; build `LcftpClient` with the minted token, the
  simulated driver (seed 1, `cloud_rate=0.2`) and a FakeClock at window
  start; `night_once(window)`; assert: every object in MinIO lies under
  `alerts/{dia}/{obs}/{plan}/` and its sha256 metadata equals the DB
  value; the number of `frames` rows with status `received` equals the
  number of `observed` outcomes × `frames_per_item`; every plan item has
  exactly one outcome; the spool's `frames/` is empty.
- [ ] **Step 2: Run; expect FAIL** until wiring gaps are fixed (fix them
  in the owning module with a unit test first).
- [ ] **Step 3: Write the Dockerfile; verify** `docker build -t lcftp:dev .`
  and `docker run --rm lcftp:dev --help` lists `web`, `worker`, `migrate`.
- [ ] **Step 4: Run** `uv run nox -s lint typing test e2e`; all PASS.
- [ ] **Step 5: Commit** — `test: end-to-end simulated night; add container image`.

### Task 24: Notebook interface

**Files:**
- Create: `client/notebooks/lcftp-quickstart.ipynb`, `tests/e2e/notebook_test.py`
- Modify: `pyproject.toml` dev group (`nbmake`, `ipykernel`)

**Interfaces:**
- Consumes: `LcftpClient`, E2E fixtures (Task 23).
- Produces: a notebook that reads `LCFTP_CLIENT_CONFIG` (path to a client
  TOML) and, with top-level `await`: shows the observatory profile, requests
  a plan for the next N hours, renders the items as a table and an
  altitude-vs-time plot (matplotlib + astroplan `plot_altitude` if
  astroplan is importable, else matplotlib only), runs it with the
  configured driver, uploads, and shows `status()`. No widgets, no extra
  dependencies beyond the client's and matplotlib.
- [ ] **Step 1: Write the failing test** that starts the E2E stack, writes
  a client config, sets `LCFTP_CLIENT_CONFIG`, and runs
  `pytest --nbmake client/notebooks/lcftp-quickstart.ipynb` in a
  subprocess; expects exit code 0.
- [ ] **Step 2: Run; expect FAIL** (notebook missing).
- [ ] **Step 3: Write the notebook** (outputs cleared before commit).
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** — `feat(client): quickstart notebook tested with nbmake`.

### Task 25: Reconcile client dependencies with the RSP image

The RSP lists are in Adam's `python-deps/` directory (supplied
2026-11-09); use `sciplat-conda-stack.yaml` (the notebook kernel).
May run any time after Task 22.

**Files:**
- Create: `docs/rsp-compatibility.md`, `client/tests/rsp_compat_test.py`,
  `client/tests/data/rsp-kernel-packages.txt` (generated from
  `sciplat-conda-stack.yaml`, `name==version` per line)
- Modify: `client/pyproject.toml` as needed

**Interfaces:**
- Produces: a test that, for every runtime requirement of `lcftp-client`
  and `lcftp-models` (read from `importlib.metadata.requires`), asserts the
  name is in the RSP list and the RSP version satisfies our specifier;
  failures list the offending packages.
- [ ] **Step 1: Add the list and write the failing test.**
- [ ] **Step 2: Run; inspect failures.**
- [ ] **Step 3: For each failure, prefer an RSP-present alternative or
  loosen our specifier; if neither is reasonable, keep the dependency and
  list it in `docs/rsp-compatibility.md` under "To RFC".**
- [ ] **Step 4: Run; expect PASS**; also run the notebook test.
- [ ] **Step 5: Commit** — `build(client): align dependencies with the RSP notebook image`.
