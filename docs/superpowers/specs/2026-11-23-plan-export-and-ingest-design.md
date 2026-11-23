# Plan export (CSV, NINA-importable) and frame ingest — design

## Intent

Observers whose telescopes lcftp cannot drive directly (NINA users first,
anyone else second) should be able to get an lcftp observing plan into
their hands, carry it out with their own software, and send the frames
back with the same provenance a driver-run night produces.

Priority order, from the requester:

1. Get plans into observers' hands (export).
2. Close the loop: get their frames back into lcftp (ingest). At minimum,
   document the filename convention now so frames taken today can be
   ingested later.

## Goals

- One CSV format that NINA's "Import Targets" accepts **and** that a
  person can read and translate for their own setup.
- A documented target-naming convention that, when it ends up in frame
  filenames or FITS headers, ties each frame unambiguously to a plan item.
- An `ingest` command that turns a directory of FITS frames into spool
  frames with full provenance, ready for `lcftp upload`.

## Non-goals

- Live control of NINA (its HTTP/advanced-API plugins). Possible later.
- NINA Advanced Sequencer JSON or `.ninaTargetSet` XML. The JSON is the
  richest but tied to .NET type names and churning in NINA 3.3; it would
  need load-testing in a real NINA. Revisit if CSV proves insufficient.
- A YAML or JSON "generic" format. CSV is what the ecosystem shares
  (Telescopius, NINA Target Scheduler, AAVSO); one format serves both.
- Server or wire-contract changes.

## Anti-goals

- **Misattributed frames.** Ingest must never guess. A frame that cannot
  be tied to a plan item by its tag (or an explicit `--item`) is reported
  and left alone, not matched by "closest target in time/sky".
- Touching observers' originals. Ingest copies; it never moves, renames,
  or modifies the source files.
- Bending the `TelescopeDriver` protocol to fit exporters.

## Approaches considered

- **A. Separate `export` / `ingest` commands (chosen).** Exporters are
  not drivers: they write a file and stop. Keeps the runner's semantics
  intact; ingest reuses the spool/uploader path.
- **B. Exporter as a `TelescopeDriver` (`driver = "nina"`).** Rejected:
  the runner waits for each window, expects frames, and would record a
  night of empty failed outcomes.
- **C. "Hand-off" driver with deferred outcomes.** Most automated
  (daemon-friendly), but needs a new outcome state and server support.
  Can be layered on A later without throwing anything away.

## Design

### Target tag

`lcftp-<plan8>-<item_id>`, e.g. `lcftp-3f9a1c2e-007`, where `plan8` is
the first 8 hex characters of `plan_id` (a `uuid4().hex`) and `item_id`
is the planner's zero-padded index. Lower-case, `[a-z0-9-]` only, so it
is safe in filenames on every OS and survives NINA's `$$TARGETNAME$$`
substitution unchanged.

### `lcftp export [PATH]`

- Writes the spool's current plan (from `lcftp plan`) as CSV to `PATH`,
  or stdout if omitted. `--plan-file FILE` exports a saved plan JSON
  instead. Errors clearly if there is no plan.
- *(Design call.)* `Spool.save_plan` archives every plan it saves to
  `plans/<plan_id>.json`, alongside the current-plan file, and export
  archives `--plan-file` plans too. Ingest can then resolve tags for any
  plan this client has seen, even after a newer plan replaces the current
  one.
- UTF-8, comma-separated, header row, one row per plan item in plan
  (time) order. RFC 4180 quoting via the `csv` module.

Columns, in order. The first five are exactly the Telescopius
observing-list headers NINA's importer recognises (case-insensitive).
Never emit a bare `Right Ascension` header: NINA's importer then
silently copies RA into Dec.

| Column | Content |
|---|---|
| `Familiar Name` | the target tag |
| `Catalogue Entry` | `diaObject <dia_object_id>` |
| `Right Ascension (J2000)` | `HHh MMm SS.SSs`, e.g. `05h 35m 17.30s` |
| `Declination (J2000)` | `±DDd MMm SS.Ss`, e.g. `-05d 23m 28.0s` |
| `Position Angle (East)` | `0` |
| `Not Before (UTC)` | ISO 8601, `Z` suffix |
| `Not After (UTC)` | ISO 8601, `Z` suffix |
| `RA (deg)` | decimal, 6 places |
| `Dec (deg)` | decimal, 6 places |
| `Filter` | `suggested_filter` or empty |
| `Total Exposure (s)` | `exposure_hint.total_exposure_s` or empty |
| `Target SNR` | `exposure_hint.target_snr` or empty |
| `Mag` | latest AB mag |
| `Band` | band of that mag |
| `Classification` | or empty |
| `Priority` | 0 = most desirable |
| `Dia Object ID` | integer |
| `Plan ID` | full `plan_id` |
| `Item ID` | `item_id` |

NINA imports names and coordinates only (it replaces its target list
and applies the user's default exposure template); the remaining
columns tell the observer what to aim for. NINA's `AstroUtil.DMSToDegrees`
pulls out the first three numbers and treats any `-` as negative, so the
`h/m/s` and `d/m/s` letters are only for human readers. *(Design call.)*
`d/m/s` is used instead of `° ' "` so no field needs CSV quoting. A
load test in a real NINA is still worth doing before announcing it.

### Observer guide (`docs/observers.md`)

Ships with export. Covers: requesting and exporting a plan; importing
into NINA (Simple Sequencer → Import targets, or via Telescopius-format
CSV into the Target Scheduler plugin); setting NINA's image file pattern
to include `$$TARGETNAME$$`; for other software, putting the tag in the
filename or in the FITS `OBJECT` header; honouring the UTC windows;
then `lcftp ingest` and `lcftp upload`.

### `lcftp ingest [DIR] [--item TAG] [--dry-run] [--force] [--final --plan PLAN8]`

- Walks `DIR` recursively for `*.fits`, `*.fit`, `*.fts` (case-insensitive).
- *(Design call.)* Frames that already carry `LCFTPPLN` came from a
  driver and went through the spool. They are skipped and counted as
  "already lcftp", never re-ingested. Re-ingesting them would create
  server duplicates under the wrong driver.
- Resolves each frame's item, in order: a tag in the filename; a tag in
  `OBJECT`; else `--item TAG` if given (for software that cannot embed
  the tag). The `plan8` prefix resolves against archived plans. An
  unknown or ambiguous prefix, or an unknown `item_id`, is an error for
  that frame.
- *(Design call.)* Ingest refuses frames from a plan whose
  `observatory_id` is not this client's: the server rejects such frames,
  and one of them would poison a whole upload batch.
- *(Design call.)* Two sanity checks guard against tag typos and reused
  tags, in line with the no-guessing anti-goal. A frame is rejected if
  its `DATE-OBS` falls outside the plan's availability window, or if its
  header pointing is more than one profile FOV from the target. If the
  `DATE-OBS` is outside the item's own window but inside the plan's, the
  frame is accepted with a warning: observers will start late. `--force`
  overrides both rejections.
- Reads `DATE-OBS` (required; UTC), `EXPTIME` or `EXPOSURE` (required),
  `FILTER` (optional), `RA`/`DEC` or `OBJCTRA`/`OBJCTDEC` for pointing
  (falling back to the plan target's coordinates), and keeps
  `TELESCOP`, `INSTRUME`, `OBSERVER` and the source path in
  `driver_metadata`. Frames missing required headers are reported and
  skipped.
- Copies each accepted frame to `spool.new_frame_path(uuid4())`, stamps
  provenance with `driver="ingest"` and the client version, computes
  checksums, and commits the sidecar — the same steps as
  `Runner._commit`, which should be factored into a shared helper.
- Idempotent: a frame whose SHA-256 (of the original file) was already
  ingested is skipped. The record of ingested hashes lives in the spool.
- Outcomes are recorded only on `--final` (requester's decision): the
  client reports each outcome once, and the server keeps the first one
  it receives, so outcomes must wait until the observer is done. Plain
  ingest runs only add frames, which upload normally. `lcftp ingest
  --final --plan PLAN8 [DIR]` ingests any remaining frames, then records
  outcomes for every item of that plan that has none yet. Items with
  frames get `observed`/`ok`, with `started_at`/`finished_at` taken from
  their earliest and latest ingested frames across all runs; the spool
  keeps per-item frame times for this. Every other item gets `skipped`
  with reason `other` and detail "no frames ingested". `--final` without
  `DIR` only closes the plan. The observer guide stresses this step, and
  `lcftp status` lists plans that have ingested frames but haven't been
  finalised.
- Prints a per-frame summary (ingested / already ingested / no tag /
  bad headers) and totals; `--dry-run` prints it without copying.

## Phasing

1. Export + target tag + observer guide.
2. Ingest (with the shared commit helper).

## Left to the builder

Module layout in `lcftp_client`, CLI option spellings beyond those
named, test fixtures, exact summary formatting.
