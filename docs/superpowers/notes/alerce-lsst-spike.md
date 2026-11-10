# ALeRCE LSST spike (Task 2)

- **Date:** 2026-11-09
- **Status:** static analysis only. `api-lsst.alerce.online` and
  `api.alerce.online` are not on this environment's egress allowlist (both
  the cloud sandbox and Adam's machine return a proxy 403), so no live
  calls were made. `docs/superpowers/notes/alerce_spike.py` repeats the
  live checks; run it anywhere with network access and update this note.

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

## Consequence

At ~100 objects per page this is 1 + 100 calls per page. If the live API
shows thousands of updated objects per 5-minute interval, a stream-based
broker (Fink/Lasair Kafka, or ALeRCE's stream) should move up the
priority list. The fixtures in `tests/data/alerce/` are **synthetic**,
shaped per the findings above, and must be replaced with recorded
responses once the live spike runs.
