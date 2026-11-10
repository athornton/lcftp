"""ALeRCE broker plugin for Rubin/LSST alerts.

The query strategy and field names come from the ALeRCE LSST spike; see
``docs/superpowers/notes/alerce-lsst-spike.md``. All knowledge of
ALeRCE's response format is confined to the ``_field`` accessors here.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from typing import Any, Protocol

from lcftp_models.candidate import BrokerRef, Candidate

from ..photometry import flux_njy_to_ab_mag
from .base import BrokerUnavailableError

__all__ = [
    "AlerceBroker",
    "create_broker",
    "datetime_to_mjd",
    "mjd_to_datetime",
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


def _field(record: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if record.get(name) is not None:
            return record[name]
    return None


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
            candidates = await asyncio.gather(
                *(self._safe_candidate(o, since, mag_limit) for o in objects)
            )
            for candidate in candidates:
                if candidate is not None:
                    yield candidate
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
    ) -> Candidate | None:
        """Convert one object, logging and skipping it on any error."""
        try:
            return await self._candidate(obj, since, mag_limit)
        except (BrokerUnavailableError, ValueError, TypeError) as e:
            logger.warning(
                "Skipping ALeRCE object %s: %s", _field(obj, "oid"), e
            )
            return None

    async def _candidate(
        self, obj: Mapping[str, Any], since: datetime, mag_limit: float
    ) -> Candidate | None:
        oid = _field(obj, "oid")
        detections: Sequence[Mapping[str, Any]] = await self._call(
            self._client.query_detections,
            survey=_SURVEY,
            oid=oid,
            format="json",
        )
        timed = [d for d in detections if _field(d, "mjd", "midpointMjdTai")]
        if not timed:
            logger.warning("Skipping ALeRCE object %s: no detections", oid)
            return None
        latest = max(
            timed, key=lambda d: float(_field(d, "mjd", "midpointMjdTai"))
        )
        when = mjd_to_datetime(float(_field(latest, "mjd", "midpointMjdTai")))
        flux = _field(latest, "psfFlux")
        ra = _field(latest, "ra") or _field(obj, "meanra")
        dec = _field(latest, "dec") or _field(obj, "meandec")
        if flux is None or float(flux) <= 0 or ra is None or dec is None:
            logger.warning(
                "Skipping ALeRCE object %s: bad flux or position", oid
            )
            return None
        mag = flux_njy_to_ab_mag(float(flux))
        if mag > mag_limit or when <= since:
            return None
        alert_id = _field(latest, "measurement_id", "diaSourceId", "candid")
        now = datetime.now(tz=UTC)
        return Candidate(
            dia_object_id=int(oid),
            sources=[BrokerRef(broker=self.name, broker_object_id=str(oid))],
            latest_alert_id=str(alert_id) if alert_id is not None else None,
            latest_alert_time=when,
            ra=float(ra) % 360,
            dec=float(dec),
            mag=mag,
            band=str(_field(latest, "band_name", "band")),
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
