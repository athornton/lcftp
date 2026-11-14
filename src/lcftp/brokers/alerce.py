"""ALeRCE broker plugin for Rubin/LSST alerts.

The query strategy and field names come from the ALeRCE LSST spike; see
``docs/superpowers/notes/alerce-lsst-spike.md``. All knowledge of
ALeRCE's response format is confined to the ``_field`` accessors here.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from enum import Enum
from importlib.metadata import version
from typing import Any, Protocol

from lcftp_models.candidate import BrokerRef, Candidate

from ..photometry import flux_njy_to_ab_mag
from .base import BrokerUnavailableError

__all__ = [
    "DETECTION_FIELDS",
    "OBJECT_FIELDS",
    "AlerceBroker",
    "MissingFieldError",
    "create_broker",
    "datetime_to_mjd",
    "mjd_to_datetime",
    "resolve_field",
]

_MJD_EPOCH = datetime(1858, 11, 17, tzinfo=UTC)
_SURVEY = "lsst"

logger = logging.getLogger(__name__)


def mjd_to_datetime(mjd: float) -> datetime:
    """Convert a Modified Julian Date to a UTC datetime."""
    return _MJD_EPOCH + timedelta(days=mjd)


def datetime_to_mjd(when: datetime) -> float:
    """Convert a datetime to a Modified Julian Date."""
    return (when - _MJD_EPOCH) / timedelta(days=1)


class AlerceClient(Protocol):
    """The subset of ``alerce.core.Alerce`` this plugin uses."""

    def query_objects(self, **kwargs: Any) -> Any:
        """Query a page of objects."""

    def query_detections(self, **kwargs: Any) -> Any:
        """Query an object's detections."""


OBJECT_FIELDS: Mapping[str, tuple[str, ...]] = {
    "oid": ("oid",),
    "ra": ("meanra",),
    "dec": ("meandec",),
    "lastmjd": ("lastmjd",),
}
"""ALeRCE object-list field names for each logical field, in priority order."""

DETECTION_FIELDS: Mapping[str, tuple[str, ...]] = {
    "mjd": ("mjd", "midpointMjdTai"),
    "flux": ("psfFlux",),
    "ra": ("ra",),
    "dec": ("dec",),
    "alert_id": ("measurement_id", "diaSourceId", "candid"),
    "band": ("band_name", "band"),
}
"""ALeRCE detection field names for each logical field, in priority order."""


class MissingFieldError(ValueError):
    """An ALeRCE record has none of the names for a logical field."""

    def __init__(self, field: str, keys: list[str]) -> None:
        self.field = field
        self.keys = keys
        super().__init__(f"no {field!r} field; record keys: {keys}")


def resolve_field(
    record: Mapping[str, Any],
    fields: Mapping[str, tuple[str, ...]],
    logical: str,
    *,
    required: bool = True,
) -> Any:
    """Return the first non-null value among a logical field's names."""
    for name in fields[logical]:
        if record.get(name) is not None:
            return record[name]
    if required:
        raise MissingFieldError(logical, sorted(record))
    return None


class _Outcome(Enum):
    FILTERED = "filtered"
    SCHEMA = "schema"


class AlerceBroker:
    """Poll ALeRCE's LSST API for recently updated, bright objects."""

    def __init__(
        self,
        client: AlerceClient | None = None,
        *,
        page_size: int = 100,
        max_pages: int = 50,
        concurrency: int = 4,
        page_delay_s: float = 0.2,
    ) -> None:
        if client is None:
            from alerce.core import Alerce  # noqa: PLC0415

            client = Alerce()
        self._client = client
        self.page_size = page_size
        self.max_pages = max_pages
        self._semaphore = asyncio.Semaphore(concurrency)
        self._page_delay_s = page_delay_s

    @property
    def name(self) -> str:
        """Broker name."""
        return "alerce"

    @property
    def version(self) -> str:
        """Version of the alerce client library."""
        try:
            return version("alerce")
        except Exception:  # pragma: no cover
            return "unknown"

    async def poll(
        self, since: datetime, mag_limit: float
    ) -> AsyncIterator[Candidate]:
        """Yield objects detected after ``since`` and brighter than limit."""
        window = [
            datetime_to_mjd(since),
            datetime_to_mjd(datetime.now(tz=UTC)),
        ]
        # ALeRCE lists an object once per classifier that classified it.
        seen: set[str] = set()
        for page in range(1, self.max_pages + 1):
            objects = await self._call(
                self._client.query_objects,
                survey=_SURVEY,
                format="json",
                lastmjd=window,
                order_by="lastmjd",
                order_mode="DESC",
                page=page,
                page_size=self.page_size,
            )
            if not objects:
                return
            unique = []
            for obj in objects:
                oid = obj.get("oid")
                if oid is None or str(oid) not in seen:
                    unique.append(obj)
                    if oid is not None:
                        seen.add(str(oid))
            results = await asyncio.gather(
                *(self._safe_candidate(o, since, mag_limit) for o in unique)
            )
            schema_errors = [
                r for r in results if isinstance(r, MissingFieldError)
            ]
            if results and len(schema_errors) == len(results):
                msg = f"ALeRCE response schema changed: {schema_errors[0]}"
                raise BrokerUnavailableError(msg)
            for result in results:
                if isinstance(result, Candidate):
                    yield result
            if self._page_delay_s:
                await asyncio.sleep(self._page_delay_s)
        logger.warning(
            "ALeRCE poll stopped at max_pages=%d; older updates were not"
            " fetched (raise LCFTP_BROKER_ALERCE_MAX_PAGES)",
            self.max_pages,
        )

    async def _call(self, method: Any, **kwargs: Any) -> Any:
        async with self._semaphore:
            try:
                return await asyncio.to_thread(method, **kwargs)
            except Exception as e:
                msg = f"ALeRCE request failed: {e}"
                raise BrokerUnavailableError(msg) from e

    async def _safe_candidate(
        self, obj: Mapping[str, Any], since: datetime, mag_limit: float
    ) -> Candidate | MissingFieldError | _Outcome:
        """Convert one object, logging and skipping it on any error.

        Returns the candidate, the `MissingFieldError` that made the object
        unusable, or `_Outcome.FILTERED` for any other skip.
        """
        oid = obj.get("oid")
        try:
            return await self._candidate(obj, since, mag_limit)
        except MissingFieldError as e:
            logger.warning("Skipping ALeRCE object %s: %s", oid, e)
            return e
        except (BrokerUnavailableError, ValueError, TypeError) as e:
            logger.warning("Skipping ALeRCE object %s: %s", oid, e)
            return _Outcome.FILTERED

    async def _candidate(
        self, obj: Mapping[str, Any], since: datetime, mag_limit: float
    ) -> Candidate | _Outcome:
        oid = resolve_field(obj, OBJECT_FIELDS, "oid")
        dia_object_id = int(str(oid))
        lastmjd = resolve_field(obj, OBJECT_FIELDS, "lastmjd", required=False)
        if lastmjd is not None and mjd_to_datetime(float(lastmjd)) <= since:
            return _Outcome.FILTERED
        detections: Sequence[Mapping[str, Any]] = await self._call(
            self._client.query_detections,
            survey=_SURVEY,
            oid=oid,
            format="json",
        )
        if not detections:
            logger.warning("Skipping ALeRCE object %s: no detections", oid)
            return _Outcome.FILTERED
        latest = max(
            detections,
            key=lambda d: float(resolve_field(d, DETECTION_FIELDS, "mjd")),
        )
        when = mjd_to_datetime(
            float(resolve_field(latest, DETECTION_FIELDS, "mjd"))
        )
        flux = float(resolve_field(latest, DETECTION_FIELDS, "flux"))
        band = str(resolve_field(latest, DETECTION_FIELDS, "band"))
        ra = resolve_field(
            latest, DETECTION_FIELDS, "ra", required=False
        ) or resolve_field(obj, OBJECT_FIELDS, "ra")
        dec = resolve_field(
            latest, DETECTION_FIELDS, "dec", required=False
        ) or resolve_field(obj, OBJECT_FIELDS, "dec")
        if not flux > 0:
            logger.warning("Skipping ALeRCE object %s: bad flux %s", oid, flux)
            return _Outcome.FILTERED
        mag = flux_njy_to_ab_mag(flux)
        if mag > mag_limit or when <= since:
            return _Outcome.FILTERED
        alert_id = resolve_field(
            latest, DETECTION_FIELDS, "alert_id", required=False
        )
        now = datetime.now(tz=UTC)
        return Candidate(
            dia_object_id=dia_object_id,
            sources=[BrokerRef(broker=self.name, broker_object_id=str(oid))],
            latest_alert_id=str(alert_id) if alert_id is not None else None,
            latest_alert_time=when,
            ra=float(ra) % 360,
            dec=float(dec),
            mag=mag,
            band=band,
            first_seen=now,
            last_updated=now,
        )


def create_broker(config: Mapping[str, str]) -> AlerceBroker:
    """Create the broker from ``LCFTP_BROKER_ALERCE_*`` configuration."""
    return AlerceBroker(
        page_size=int(config.get("page_size", "100")),
        max_pages=int(config.get("max_pages", "50")),
        concurrency=int(config.get("concurrency", "4")),
        page_delay_s=float(config.get("page_delay_s", "0.2")),
    )
