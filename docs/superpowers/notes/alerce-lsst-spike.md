# ALeRCE LSST spike (Task 2)

- **Date:** 2026-11-09
- **Status:** live recording 2026-11-14T01:30Z (`alerce` 2.4.0), made
  with `scripts/record-alerce-fixtures.py` into
  `tests/data/alerce/recorded/`. The findings below were originally static
  analysis; the "Live results" section records what the API actually
  returned.

## Sources

- `alerce` 2.4.0 client source (`alerce/ms_search.py`,
  `alerce/default_config.json`).
- Adam's prototype targeter (removed in Task 1, see `c7972a4`), which ran
  against the live LSST API: it paged `query_objects(survey="lsst",
  format="json", page=p)`, read `oid`, `meanra`, `meandec` from each
  object, and read `psfFlux` (nJy) from
  `query_lightcurve(survey="lsst", oid=oid)["detections"][0]`.

## Findings

1. LSST queries go to `https://api-lsst.alerce.online/` (the
   "multisurvey" service). `survey="lsst"` is required.
2. `query_objects` accepts exactly: `oid, survey, classifier, class_name,
   ranking, n_det, probability, firstmjd, lastmjd, ra, dec, radius, page,
   page_size, count, order_by, order_mode`. **There is no magnitude
   filter**, so brightness must be read per object.
3. Time filtering is server-side: `lastmjd` (as in the ZTF API, a
   `[min, max]` pair) and `order_by="lastmjd", order_mode="DESC"`.
4. Per-object photometry: `query_detections(survey="lsst", oid=...)`
   returns a list of detections; each has `band` (int) plus `band_name`
   (added by the client from `band_map`), and — per the prototype —
   `psfFlux` in nJy. One extra call per object is unavoidable.
5. `oid` is treated as the Rubin `diaObjectId` (the prototype stores it as
   `int`). **Unverified live**; the plugin isolates this in one accessor.
6. Errors: `alerce.exceptions.APIError` (and subclasses) on HTTP ≥ 400;
   `requests` exceptions on network failure.

## Decision: poll algorithm (inputs `since`, `mag_limit`)

1. `now_mjd = mjd(now)`, `since_mjd = mjd(since)`.
2. For `page` in 1..`max_pages` (default 50): `query_objects(survey="lsst",
   format="json", lastmjd=[since_mjd, now_mjd], order_by="lastmjd",
   order_mode="DESC", page=page, page_size=100)`; stop on an empty page.
3. For each object, with at most `concurrency` (default 4) calls in
   flight: `query_detections(survey="lsst", oid=oid, format="json")`;
   take the detection with the greatest `mjd`; skip if `psfFlux <= 0`
   or coordinates are missing; `mag = 31.4 − 2.5·log10(psfFlux)`; yield
   a `Candidate` if `mag <= mag_limit` and the detection is after `since`.
4. Field accessors (all in one place in `lcftp.brokers.alerce`):
   object id `oid`; position `meanra`/`meandec`; detection time `mjd`
   (fallback `midpointMjdTai`); alert id `measurement_id` (fallbacks
   `diaSourceId`, `candid`); band `band_name` (fallback `band`).
5. Rate limit: no more than `concurrency` simultaneous detection calls;
   `page_delay_s` (default 0.2 s) between object pages.

## Live results (2026-11-14)

Recorded with a 1-day `lastmjd` window, 2 pages of 10.

- **Server-side window works.** Every returned object's `lastmjd` was
  inside the requested window and equal to its latest detection's `mjd`
  (finding 3 confirmed).
- **Detection field names match finding 4.** `mjd`, `psfFlux` (nJy),
  `ra`, `dec`, `measurement_id`, `band` (int) and `band_name` are all
  present; no field-table changes were needed. Detections also carry
  `diaObjectId`, `midpointMjdTai` is absent, and `measurement_id` is a
  diaSourceId-sized integer.
- **`oid` is the `diaObjectId`** (finding 5 verified): every recorded
  detection's `diaObjectId` equals its object's `oid`. The single-object
  endpoint (`object.json`) does not carry a `diaObjectId` key.
- **The object list has one row per (object, classifier).** 20 rows were
  15 distinct objects: the `stamp_classifier_rubin_beta` and
  `stamp_classifier_rubin_beta_20260421` classifiers each contribute a
  row. The plugin now de-duplicates by `oid` within a poll, so each
  object costs one detections call.
- **Rows carry a classification**: `class_name` (seen: `AGN`, `VS`,
  `bogus`), `classifier_name`, `classifier_version`, `probability`. 3 of
  15 objects were `bogus`. Nothing uses this yet.
- **Negative PSF flux is common** (8 of 15 latest detections): difference
  imaging reports fading sources as negative flux. The plugin skips them
  (correct for a "brighter than" filter) but logs each at WARNING.
- All 15 objects were faint (the brightest positive-flux latest detection
  was ~21.2 mag in `i`), so a typical amateur `mag_limit` keeps none of
  this sample.

## Consequence

At ~100 objects per page this is 1 + 100 calls per page. Measured
2026-11-14 (`count=true`): **53,888 object rows** had `lastmjd` in the
previous 24 hours (about 27,000 distinct objects after the
two-classifier duplication), an average of ~190 rows per 5-minute ingest
interval, while the most recent 5 minutes held none (ALeRCE ingestion
lags real time and is bursty). An average poll is therefore ~2 pages and
~100 detections calls; a night-time burst can hit `max_pages`
(5,000 rows). Because there is no server-side brightness filter, almost
all of those calls are for objects fainter than any amateur limit, so a
stream-based broker (Fink/Lasair Kafka, or ALeRCE's stream) with a
magnitude filter remains the better long-term source.

The fixtures directly in `tests/data/alerce/` are **synthetic** edge-case
fixtures and are kept on purpose; `tests/data/alerce/recorded/` holds the
live recording.
