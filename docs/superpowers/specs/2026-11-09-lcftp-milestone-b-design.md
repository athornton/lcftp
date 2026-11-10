# LCFTP Milestone B — Design Spec

- **Date:** 2026-11-09
- **Status:** Draft, awaiting review
- **Author:** Adam Thornton (with Claude)
- **Background:** [The Manifesto](../../MANIFESTO.md)

## 1. Purpose and context

Los Cumbres For The People (LCFTP) matches Rubin/LSST alerts with amateur
robotic telescopes that can observe them, so the global amateur community
can do follow-up the small professional community has no telescope time
for. Conceptually it is SETI@Home or BHTOM for optical follow-up.

**Priorities.** Participation and engagement come first: it must be easy
for amateurs to join and worth their while. Scientific usefulness is a
close second: data must carry enough provenance and metadata that it can
go to a clearinghouse (likely Zooniverse) and support a co-authorship
policy ("if your data confirmed this object and a paper resulted, you are
a co-author"). **Attribution is a core requirement, not a nice-to-have.**

**Users.** Amateur astronomers with robotic telescopes, from SeeStar S50
class (~$500) through 30–70 cm domes up to professional-grade
instruments (e.g. Fairborn Observatory's 2 m). Early users are mostly in
the Northern Hemisphere, while Rubin (Cerro Pachón) surveys the southern
sky, so per-site visibility filtering does most of the work.

**Owner control.** Observatory owners decide when we may use their
equipment and can halt us at any time.

## 2. Milestone scope

### Milestone B (this spec)

A working proof of concept without real telescope hardware:

1. GitHub OAuth2 login restricted to a GitHub organization the operator
   controls.
2. Observatory registration (profile + capabilities).
3. Broker ingest via a pluggable broker layer; ALeRCE (`survey="lsst"`)
   is the only implementation.
4. Per-observatory observation plans for a stated availability window.
5. The Raspberry Pi client, with a pluggable driver layer and a
   **simulated** telescope driver that produces realistic simulated FITS.
6. Direct-to-bucket upload with integrity verification and provenance.
7. A JupyterLab notebook interface to the client, compatible with the
   Rubin Science Platform (RSP) notebook environment.

### Milestone A (next; begins when the SeeStar arrives)

A `seestar` driver plugin built on
[seestarpy](https://seestarpy.readthedocs.io) and end-to-end operation
on real hardware. Milestone B must make this primarily a matter of
writing one driver.

### Out of scope for now

Clearinghouse/Zooniverse integration; co-authorship workflow; additional
brokers (Fink, ANTARES, Lasair, …); Kafka stream ingest; non-SeeStar
real drivers (e.g. Fairborn); a polished web UI beyond what registration
and token management need; Phalanx/Gafaelfawr deployment (designed for,
not done).

## 3. Engineering constraints

- **Language:** Python ≥ 3.14 for the server and `lcftp-auth`; Python ≥
  3.13 for `lcftp-models` and `lcftp-client`, because the RSP notebook
  kernel is Python 3.13 (decided 2026-11-09). CI runs 3.14 and 3.15 for
  everything and additionally 3.13 for models and client. No older
  backports.
- **Typing:** very strict — `mypy` with the strictness options already
  in `pyproject.toml` plus the pydantic plugin; `ruff` with
  `ruff-shared.toml`. The Ruff rule set starts from
  [lander's `ruff-shared.toml`](https://github.com/lsst-sqre/lander/blob/main/ruff-shared.toml)
  (SQuaRE house style, to ease a future move into Phalanx), replacing
  the current Gafaelfawr-derived file, with only these adjustments:
  `target-version` set to `py314` (lander pins `py312`); the renamed
  `TCH001–003` codes updated to `TC001–003` for current Ruff; and
  per-file ignores for `tests/**`, `alembic/**` and `noxfile.py`
  (asserts, docstrings, `print`). No untyped defs; `Any` only at third-party
  boundaries and immediately narrowed by Pydantic validation.
- **Concurrency:** asyncio throughout. Blocking third-party calls
  (e.g. the synchronous `alerce` client, possibly `seestarpy`) are wrapped
  with `asyncio.to_thread` behind async interfaces.
- **Server stack:** FastAPI + Pydantic (v2), SQLAlchemy 2 async +
  asyncpg, Alembic, `httpx`. Safir is deliberately not used for the MVP.
- **Methodology:** strict TDD — every behavior begins as a failing test.
- **License:** MIT. **Repository:** <https://github.com/athornton/lcftp>.
- **Deployment:** one container image configured entirely by
  environment variables, runnable standalone (Docker) now and as a
  Phalanx application later (Helm-friendly: env config, health
  endpoints, separate web/worker processes from the same image).
- **RSP compatibility:** client and notebook dependencies should be drawn
  from libraries present in the RSP notebook kernel where possible. The
  RSP package lists (`sciplat-conda-stack.yaml` for the kernel) were
  supplied on 2026-11-09; every client and models runtime dependency is
  present there (pydantic 2.14, httpx 0.28.1, click 8.5, astropy 8.0.2,
  astroplan 0.10.1, numpy 2.4.6, matplotlib 3.10.9, google-crc32c 1.9,
  tomli-w 1.2). Anything missing is to be proposed to the RSP by RFC.

## 4. Architecture

### 4.1 Repository layout

A uv workspace, building on the existing scaffolding (`pyproject.toml`,
`noxfile.py`, `.pre-commit-config.yaml` already anticipate a separate
`client/` project):

```
lcftp/
├── pyproject.toml        # workspace root; the server package "lcftp"
├── src/lcftp/            # lcftp-server
├── models/               # lcftp-models  (workspace member)
├── auth/                 # lcftp-auth    (workspace member)
├── client/               # lcftp-client  (workspace member; own noxfile)
├── docs/
└── tests/
```

Dependency direction: `lcftp-models` depends on nothing of ours.
`lcftp-auth` depends on `lcftp-models`. The server depends on both.
The client depends only on `lcftp-models` — **never** on the server or
its dependencies, so the Pi install stays small.

The prototype `src/lcftp/services/svc.py` and the dataclasses in the
local `src/Observation.py` / `src/observatory.py` are superseded by this
design; useful logic (ALeRCE paging, flux→magnitude) is re-implemented
test-first in the appropriate modules.

### 4.2 `lcftp-models` — the contract

Pydantic models shared by server and client. All are versioned via the
JSON Schema snapshot (§8). Core models:

- `SiteLocation` — latitude, longitude (deg), elevation (m), timezone.
  Convertible to an `astroplan.Observer` (the server performs that
  conversion; the models package itself does not import astroplan, to
  keep the client light).
- `ObservatoryProfile` — `id`, `name`, `owner` (GitHub login),
  `site: SiteLocation`, `aperture_m`, `limiting_mag` (faintest
  observable magnitude under typical conditions), `fov_arcmin`,
  `filters: list[FilterSpec]` (name + effective wavelength nm),
  `driver` (entry-point name), `constraints` (min altitude, min moon
  separation, max airmass; defaults provided).
- `Candidate` — normalized broker output: `dia_object_id` (Rubin
  `diaObjectId`, the cross-broker identity), `sources:
  list[BrokerRef]` (broker name, broker object id), `latest_alert_id`,
  `latest_alert_time`, `ra`, `dec`, `mag`, `band`, optional
  `classification` and `score`, `first_seen`, `last_updated`.
- `AvailabilityWindow` — `start`, `end` (UTC, timezone-aware, `end >
  start`, max length 24 h).
- `ObservationPlan` — immutable: `plan_id`, `observatory_id`,
  `window`, `created_at`, `candidates_as_of`, `planner_version`,
  `items: list[PlanItem]`. Supersession is tracked server-side (§5), not
  in the immutable plan document.
- `PlanItem` — *intent, not commands*: `item_id`, `candidate`
  (snapshot of the `Candidate` used), `not_before`, `not_after`,
  `suggested_filter`, `exposure_hint` (`total_exposure_s` and/or
  `target_snr`), `priority`.
- `ItemOutcome` — `item_id`, `status` (`observed` | `skipped` |
  `failed`), `reason` (enum + free text), `started_at`, `finished_at`.
- `FrameRecord` — `frame_id` (UUID), `plan_id`, `item_id`,
  `observatory_id`, `dia_object_id`, `alert_id`, `obs_start`,
  `exposure_s`, `filter`, `driver` name + version, actual pointing
  (RA/Dec), `sha256`, `size_bytes`, backend-native checksum, and a
  free-form `driver_metadata` dict (validated JSON-compatible).
- `UploadManifest` — `batch_id`, `frames: list[FrameRecord]`,
  `outcomes: list[ItemOutcome]`.
- `ErrorResponse` — `code` (stable machine-readable enum), `message`,
  optional `detail`.

### 4.3 `lcftp-auth`

- **Browser login:** GitHub OAuth2 authorization-code flow. After
  login, the server checks membership of the configured GitHub
  organization (`read:org` scope); non-members are refused. A signed,
  HTTP-only session cookie identifies the user.
- **Device tokens:** a logged-in user mints a token for one of their
  observatories. Shown once; stored as a hash; scoped to that
  observatory; revocable; has a last-used timestamp. The client sends it
  as `Authorization: Bearer`.
- **Interface:** FastAPI dependencies `require_user` and
  `require_device(observatory_id)` returning typed principals. The rest
  of the server depends only on these, so the implementation can later
  be replaced by Gafaelfawr-provided identity headers under Phalanx.

### 4.4 `lcftp-server`

Modules:

- `api` — FastAPI routers: `/auth/*`, `/observatories`,
  `/observatories/{id}/tokens`, `/observatories/{id}/availability`,
  `/plans/{plan_id}`, `/uploads`, `/uploads/{batch_id}/complete`,
  `/health`, `/health/ready`. Minimal server-rendered HTML pages for
  login, observatory registration and token management.
- `brokers` — the `BrokerSource` protocol and plugin loading via the
  `lcftp.brokers` entry-point group. Protocol:
  `async def poll(since: datetime, mag_limit: float) ->
  AsyncIterator[Candidate]`, plus `name` and `version`.
  - `brokers.alerce` — the only implementation. Uses the `alerce`
    client with `survey="lsst"` (always passed explicitly; the default
    is deprecated). Converts PSF flux in nJy to AB magnitude with
    `m = 31.4 − 2.5·log10(flux_nJy)`. (The prototype's conversion has a
    sign error in the `1e9` factor; tests pin the correct formula with
    known values, e.g. 3631 Jy → 0.0 mag, 1 nJy → 31.4 mag.) The exact
    query strategy for "objects updated since T, brighter than M"
    depends on what ALeRCE's LSST API supports server-side; the
    implementation plan begins with a short spike to determine it
    (see §10).
- `ingest` — the worker loop. For each configured broker, polls
  incrementally from the last high-water mark with an **adaptive
  magnitude cutoff** = (faintest `limiting_mag` among registered
  observatories) + margin (default 0.5 mag), upserts candidates keyed
  by `dia_object_id`, and expires candidates not updated within a
  configurable age (default 7 days). Records `candidates_as_of`.
- `planner` — given an `ObservatoryProfile` and `AvailabilityWindow`,
  selects candidates with `mag ≤ limiting_mag`, computes per-target
  observable intervals within the window using astroplan (altitude,
  airmass, moon separation, astronomical twilight), ranks them, and
  greedily packs a time-ordered plan. Ranking (MVP): brightness margin
  below the limit, recency of the latest alert, and a penalty for
  targets already assigned to ≥ N observatories for the same night
  (default N = 3). Planner is a pure function of its inputs plus a
  clock — no I/O — so it is deterministic and directly testable.
- `storage` — `ObjectStore` protocol with `sign_upload(key, size,
  checksum) -> SignedUpload` and `stat(key) -> ObjectStat | None`.
  Implementations: `gcs` (Google Cloud Storage; binds `x-goog-hash`
  CRC32C/MD5 into the signed URL so mismatched content is rejected) and
  `s3` (S3-compatible; binds `x-amz-checksum-sha256`). An in-memory
  fake for tests. Object key:
  `alerts/{dia_object_id}/{observatory_id}/{plan_id}/{frame_id}.fits`.
  The sha256 is also written as object custom metadata.
- `db` — SQLAlchemy async models and Alembic migrations: users,
  observatories, device tokens (hashed), candidates (+ broker refs),
  plans (immutable JSON snapshot + indexed columns), assignments,
  upload batches, frames, outcomes, ingest high-water marks.
- `config` — a Pydantic Settings object read from environment
  variables (prefix `LCFTP_`).

**Processes** (one image, three entry points): `lcftp web` (uvicorn),
`lcftp worker` (ingest + expiry loop), `lcftp migrate` (Alembic).

### 4.5 `lcftp-client`

- **Library core** (`lcftp_client`): an async API —
  `Client.request_plan(window)`, `Runner.run(plan)`,
  `Uploader.flush()`, `Client.status()`, `Runner.stop()`. The CLI and
  the notebook are both thin layers over this.
- **Drivers:** the `TelescopeDriver` protocol, loaded via the
  `lcftp.drivers` entry-point group: `connect()`, `disconnect()`,
  `async observe(item: PlanItem, spool: Spool) -> DriverResult`
  (frames written + actual parameters used), `async park()`, `name`,
  `version`, `capabilities()`. Drivers translate intent (`PlanItem`)
  into hardware-specific actions. Milestone B ships the `simulated`
  driver only.
- **Simulated driver:** generates FITS images (astropy) of a plausible
  star field with a source at the target position whose brightness
  follows the candidate magnitude, with realistic WCS and observing
  headers. Supports fault injection (configurable timeout, failure,
  "clouds" probability, seeded RNG) for testing.
- **Spool:** on-disk directory holding the current plan, per-item
  state, FITS frames and `FrameRecord` sidecar JSON. All state
  transitions are written atomically (write-temp + rename) so a crash at
  any point is recoverable. Frames are deleted only after server
  confirmation.
- **FITS headers** stamped on every frame: `LCFTPPLN` (plan id),
  `LCFTPITM` (item id), `LCFTPOBS` (observatory id), `LCFTPDIA`
  (diaObjectId), `LCFTPALR` (alert id), `LCFTPDRV` (driver name/version),
  plus standard `DATE-OBS`, `EXPTIME`, `FILTER`, WCS.
- **CLI:** `lcftp-client init --server URL --token TOKEN`,
  `lcftp-client plan --start … --end …`, `lcftp-client run`,
  `lcftp-client upload`, `lcftp-client daemon` (plan → run → upload
  loop around a configured nightly window), `lcftp-client stop`,
  `lcftp-client status`.
- **Notebook:** an example notebook that uses the library API with
  top-level `await` to request a plan, inspect it (table of targets,
  altitude curves), run it with the simulated driver, and upload. Runs
  in a stock JupyterLab and on the RSP.

## 5. Data flow

1. **Onboarding (web, once).** User logs in with GitHub (org member
   only) → creates an `ObservatoryProfile` → mints a device token →
   runs `lcftp-client init` on the Pi.
2. **Availability → plan (online).** Client `POST
   /observatories/{id}/availability` with an `AvailabilityWindow`. The
   server synchronously plans from the candidate cache and returns an
   `ObservationPlan`. Plans are immutable and snapshot the candidate
   data they used. A later request for an overlapping window issues a
   new plan; the server records the previous one as superseded, and
   `GET /plans/{plan_id}` reports that status alongside the unchanged plan. An empty plan is
   a valid result.
3. **Execution (offline).** The client persists the plan, executes
   items in time order, skips items whose `not_after` has passed, and
   records an `ItemOutcome` for every item. Frames go to the spool with
   stamped headers and a `FrameRecord` sidecar including sha256.
4. **Upload (when connected, resumable).** Client `POST /uploads` with
   an `UploadManifest` → server validates that frames belong to plans
   issued to this observatory and returns one signed PUT URL per frame,
   each bound to the frame's checksum → client PUTs directly to the
   bucket → client `POST /uploads/{batch_id}/complete` → server `stat`s
   each object and compares size and stored checksum to the manifest,
   records frames as received, and stores outcomes. Every step is
   idempotent by `batch_id` / `frame_id`.

## 6. Integrity and provenance

- The client computes **sha256** (our canonical provenance checksum)
  plus the backend-native checksum (CRC32C/MD5 for GCS, sha256 for S3).
- Signed upload URLs are **bound to the checksum**, so the bucket
  rejects mismatched content at upload time.
- On completion, the server verifies the stored object's checksum via a
  metadata-only `stat` — it never downloads frames.
- sha256 is stored in the database and in object custom metadata.
  Downstream consumers verify against the database/API record (a trusted
  source), not a sidecar file next to the object.
- Every frame is traceable to: user (GitHub login), observatory, plan,
  plan item, broker(s), diaObjectId, alert id, candidate snapshot, driver
  name/version, and timestamps. This is the basis for future credit and
  co-authorship.

## 7. Error handling

**Server**
- Broker poll failures: exponential backoff, logged, ingest continues
  with other brokers; plans continue from the existing cache.
  `/health/ready` reports cache age; plans carry `candidates_as_of`.
- All API errors are `ErrorResponse` with a stable `code`.
- An empty plan is not an error.
- Malformed broker records are logged and skipped, never fatal.

**Client (offline)**
- A driver error or timeout marks the item `failed` with a reason and
  execution continues; one bad target never ends the night.
- Crash/restart resumes the current plan from spool state.
- `stop` (CLI, SIGTERM/SIGINT, or notebook call) asks the driver to
  `park()` and halts the plan cleanly; remaining items are recorded as
  `skipped` with reason `stopped`.

**Upload**
- Network errors: exponential backoff with jitter.
- Expired signed URL: re-request for the affected frames.
- Checksum mismatch: re-upload once; then mark the frame `bad`, keep it
  in the spool and report it.
- 401/403: stop uploading, keep the spool, surface a clear message.
  Auth errors never cause data deletion.

**Auth**
- Expired or revoked tokens return 401 with an explanatory message.

## 8. Testing

- **Process:** strict TDD with pytest + pytest-asyncio.
- **CI (GitHub Actions):** ruff, mypy, pytest on Python 3.14 and 3.15;
  run through nox (the existing `noxfile.py`, adapted from Gafaelfawr,
  is updated for this project).
- **Models:** validation and round-trip tests; a committed **JSON
  Schema snapshot** of all contract models, so any contract change must
  be deliberate.
- **Server API:** `httpx.AsyncClient` with `ASGITransport`; real
  PostgreSQL via testcontainers (CI service container acceptable).
- **Planner:** deterministic tests with fixed sites, times and
  candidates; IERS/astropy data pinned for offline use so tests never
  hit the network. Cases include: a far-southern target is never
  planned for a northern site where it never rises; too-faint targets
  are excluded; altitude, airmass and moon constraints hold; plans are
  time-ordered and non-overlapping; the per-target observatory cap is
  respected.
- **Brokers:** a shared broker-contract test suite every plugin must
  pass; ALeRCE tested against recorded responses (via `respx` or by
  stubbing the threaded `alerce` client); a fake broker used everywhere
  else. Flux→magnitude conversion pinned with known values.
- **Storage:** in-memory fake for unit tests; integration tests against
  `fake-gcs-server` and MinIO proving the bucket rejects bad checksums.
- **Auth:** GitHub OAuth mocked with `respx` (member / non-member);
  full device-token lifecycle.
- **Client:** shared driver-contract suite; simulated driver with fault
  injection; spool crash/resume tests; upload retry/resume/expired-URL
  tests against fakes.
- **End to end:** server + worker + PostgreSQL + fake storage + fake
  broker, and a simulated client that registers, plans, observes a
  night, and uploads; asserts objects exist under the correct keys with
  matching checksums and outcomes are recorded.
- **Notebook:** the example notebook executed in CI with `nbmake`
  against the end-to-end stack.

## 9. Configuration (server, environment variables)

`LCFTP_DATABASE_URL`, `LCFTP_GITHUB_CLIENT_ID`,
`LCFTP_GITHUB_CLIENT_SECRET`, `LCFTP_GITHUB_ORG`,
`LCFTP_SESSION_SECRET`, `LCFTP_BASE_URL`, `LCFTP_STORAGE_BACKEND`
(`gcs` | `s3`), `LCFTP_STORAGE_BUCKET`, backend credentials (standard
GCP/AWS mechanisms), `LCFTP_BROKERS` (list of enabled broker plugin
names; default `["alerce"]`), `LCFTP_INGEST_INTERVAL` (default 5 min),
`LCFTP_INGEST_MAG_MARGIN` (default 0.5), `LCFTP_CANDIDATE_MAX_AGE`
(default 7 days), `LCFTP_TARGET_OBSERVATORY_CAP` (default 3).

## 10. Risks and open questions

1. **ALeRCE LSST query capabilities.** The prototype pages through
   `query_objects` and then fetches a light curve per object to get flux
   — far too many calls at Rubin volumes. The first plan task is a spike
   to learn whether ALeRCE's LSST API can filter by last-detection time
   and magnitude server-side (or supply the latest magnitude in object
   results). If it cannot, the ALeRCE plugin will restrict polls by time
   window and rate-limit light-curve lookups, and a stream-based broker
   becomes a higher priority.
2. **Volume for deep observatories.** A 2 m telescope raises the
   adaptive cutoff to ~22 mag, approaching the full alert volume. The
   MVP accepts this; if it becomes a problem the cutoff can be capped
   per broker and deep sites served by a dedicated broker filter.
3. **Simulated FITS realism.** Good enough to exercise the pipeline and
   headers; not intended for science validation.
4. **RSP dependency drift.** The kernel's package versions change over
   time; a test pins our client requirements against a committed copy
   of the kernel list.
5. **Repository push access** from the development environment needs
   to be arranged before the first commit lands on GitHub.
