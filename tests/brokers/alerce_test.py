import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from importlib.metadata import entry_points
from typing import Any, override

import pytest
import requests

from lcftp.brokers.alerce import (
    DETECTION_FIELDS,
    AlerceBroker,
    MissingFieldError,
    create_broker,
    mjd_to_datetime,
    resolve_field,
)
from lcftp.brokers.base import BrokerUnavailableError
from lcftp.brokers.testing import assert_broker_contract
from lcftp.photometry import flux_njy_to_ab_mag
from tests.support.alerce import DATA, StubAlerce

SINCE = mjd_to_datetime(61353.0)


def _expected(mag_limit: float) -> set[int]:
    expected = set()
    detections = json.loads((DATA / "detections.json").read_text())[
        "detections"
    ]
    for oid, dets in detections.items():
        latest = max(dets, key=lambda d: d["mjd"])
        if latest["psfFlux"] <= 0 or mjd_to_datetime(latest["mjd"]) <= SINCE:
            continue
        if flux_njy_to_ab_mag(latest["psfFlux"]) <= mag_limit:
            expected.add(int(oid))
    return expected


def _broker(stub: StubAlerce) -> AlerceBroker:
    return AlerceBroker(stub, page_delay_s=0)


@pytest.mark.parametrize("mag_limit", [18.0, 20.0, 30.0])
async def test_contract_and_selection(mag_limit: float) -> None:
    found = await assert_broker_contract(
        _broker(StubAlerce()), since=SINCE, mag_limit=mag_limit
    )
    assert {c.dia_object_id for c in found} == _expected(mag_limit)


async def test_candidate_fields() -> None:
    found = [c async for c in _broker(StubAlerce()).poll(SINCE, 17.0)]
    assert len(found) == 1
    c = found[0]
    assert c.dia_object_id == 1003
    assert (c.ra, c.dec) == (120.0, -60.0)
    assert c.band == "r"
    assert c.latest_alert_id == "100300"
    assert c.mag == pytest.approx(16.4)
    assert c.sources[0].broker == "alerce"
    assert c.sources[0].broker_object_id == "1003"


async def test_always_passes_lsst_survey() -> None:
    stub = StubAlerce()
    [c async for c in _broker(stub).poll(SINCE, 30.0)]
    assert stub.calls
    assert all(kwargs["survey"] == "lsst" for _, kwargs in stub.calls)
    objects_calls = [k for name, k in stub.calls if name == "query_objects"]
    assert objects_calls[0]["order_by"] == "lastmjd"
    assert objects_calls[0]["lastmjd"][0] == pytest.approx(61353.0)
    assert [k["page"] for k in objects_calls] == [1, 2, 3]


async def test_network_error_is_unavailable() -> None:
    stub = StubAlerce(error=requests.ConnectionError("down"))
    with pytest.raises(BrokerUnavailableError):
        [c async for c in _broker(stub).poll(SINCE, 30.0)]


async def test_negative_flux_is_skipped_quietly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        found = [c async for c in _broker(StubAlerce()).poll(SINCE, 30.0)]
    assert 1005 not in {c.dia_object_id for c in found}
    assert not any("1005" in r.getMessage() for r in caplog.records)


def test_mjd_round_trip() -> None:
    assert mjd_to_datetime(61353.0) == datetime(2026, 11, 9, tzinfo=UTC)
    assert mjd_to_datetime(61353.5) - mjd_to_datetime(61353.0) == timedelta(
        hours=12
    )


def test_factory_reads_config() -> None:
    broker = create_broker({"page_size": "25", "max_pages": "3"})
    assert broker.name == "alerce"
    assert broker.page_size == 25
    assert broker.max_pages == 3


def test_alerce_is_registered_entry_point() -> None:
    names = {ep.name for ep in entry_points(group="lcftp.brokers")}
    assert "alerce" in names


class MessyAlerce(StubAlerce):
    """Adds a malformed object and one whose detections request fails."""

    @override
    def query_objects(self, **kwargs: Any) -> list[dict[str, Any]]:
        objects = super().query_objects(**kwargs)
        if kwargs["page"] == 1:
            objects = [
                {"oid": 9001, "meandec": 95.0, "meanra": 10.0},
                {"oid": 9002, "meandec": 0.0, "meanra": 10.0},
                *objects,
            ]
        return objects

    @override
    def query_detections(self, **kwargs: Any) -> list[dict[str, Any]]:
        if kwargs["oid"] == 9001:
            return [{"mjd": 61353.5, "psfFlux": 1e6, "dec": 95.0, "ra": 10.0}]
        if kwargs["oid"] == 9002:
            raise requests.HTTPError("404 Not Found")
        return super().query_detections(**kwargs)


async def test_bad_objects_are_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        found = [c async for c in _broker(MessyAlerce()).poll(SINCE, 30.0)]
    assert {c.dia_object_id for c in found} == _expected(30.0)
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "9001" in messages
    assert "9002" in messages


async def test_page_limit_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    broker = AlerceBroker(StubAlerce(), page_delay_s=0, max_pages=1)
    with caplog.at_level(logging.WARNING):
        [c async for c in broker.poll(SINCE, 30.0)]
    assert any("max_pages" in r.getMessage() for r in caplog.records)


def test_resolve_field_reports_logical_name_and_keys() -> None:
    with pytest.raises(MissingFieldError) as e:
        resolve_field({"psf_flux": 1.0, "mjd": 1.0}, DETECTION_FIELDS, "flux")
    assert e.value.field == "flux"
    assert e.value.keys == ["mjd", "psf_flux"]
    assert "flux" in str(e.value)
    assert "psf_flux" in str(e.value)


def test_resolve_field_optional_returns_none() -> None:
    assert (
        resolve_field({}, DETECTION_FIELDS, "alert_id", required=False) is None
    )


def test_resolve_field_uses_fallback_names() -> None:
    assert (
        resolve_field({"midpointMjdTai": 5.0}, DETECTION_FIELDS, "mjd") == 5.0
    )


Transform = Callable[[dict[str, Any]], dict[str, Any]]


def _rename(old: str, new: str) -> Transform:
    def transform(det: dict[str, Any]) -> dict[str, Any]:
        det = dict(det)
        det[new] = det.pop(old)
        return det

    return transform


def _drop(*names: str) -> Transform:
    def transform(det: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in det.items() if k not in names}

    return transform


class SchemaAlerce(StubAlerce):
    """Rewrites detections (for some oids) and optionally adds objects."""

    def __init__(
        self,
        transform: Transform,
        *,
        oids: set[str] | None = None,
        extra_objects: list[dict[str, Any]] | None = None,
        oid_as_string: bool = False,
    ) -> None:
        super().__init__()
        self.transform = transform
        self.oids = oids
        self.extra_objects = extra_objects or []
        self.oid_as_string = oid_as_string

    @override
    def query_objects(self, **kwargs: Any) -> list[dict[str, Any]]:
        objects = super().query_objects(**kwargs)
        if self.oid_as_string:
            objects = [{**o, "oid": str(o["oid"])} for o in objects]
        if kwargs["page"] == 1:
            objects = [*self.extra_objects, *objects]
        return objects

    @override
    def query_detections(self, **kwargs: Any) -> list[dict[str, Any]]:
        dets = super().query_detections(**kwargs)
        if self.oids is None or str(kwargs["oid"]) in self.oids:
            dets = [self.transform(d) for d in dets]
        return dets


async def test_missing_band_is_skipped_not_stored_as_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stub = SchemaAlerce(_drop("band", "band_name"), oids={"1003"})
    with caplog.at_level(logging.WARNING):
        found = [c async for c in _broker(stub).poll(SINCE, 30.0)]
    assert 1003 not in {c.dia_object_id for c in found}
    assert all(c.band != "None" for c in found)
    assert any(
        "1003" in r.getMessage() and "band" in r.getMessage()
        for r in caplog.records
    )


async def test_string_oid_is_converted() -> None:
    stub = SchemaAlerce(lambda d: d, oid_as_string=True)
    found = [c async for c in _broker(stub).poll(SINCE, 30.0)]
    assert {c.dia_object_id for c in found} == _expected(30.0)


async def test_non_numeric_oid_is_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stub = SchemaAlerce(
        lambda d: d,
        extra_objects=[{"oid": "ZTF26abc", "meanra": 1.0, "meandec": 1.0}],
    )
    stub.detections["ZTF26abc"] = stub.detections["1003"]
    with caplog.at_level(logging.WARNING):
        found = [c async for c in _broker(stub).poll(SINCE, 30.0)]
    assert {c.dia_object_id for c in found} == _expected(30.0)
    assert any("ZTF26abc" in r.getMessage() for r in caplog.records)


async def test_renamed_field_on_every_object_raises_unavailable() -> None:
    stub = SchemaAlerce(_rename("psfFlux", "psf_flux"))
    with pytest.raises(BrokerUnavailableError) as e:
        [c async for c in _broker(stub).poll(SINCE, 30.0)]
    assert "flux" in str(e.value)
    assert "psf_flux" in str(e.value)


async def test_one_bad_object_does_not_raise() -> None:
    stub = SchemaAlerce(_rename("psfFlux", "psf_flux"), oids={"1003"})
    found = [c async for c in _broker(stub).poll(SINCE, 30.0)]
    assert {c.dia_object_id for c in found} == _expected(30.0) - {1003}


async def test_stale_objects_skip_detection_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stale = {"oid": 1999, "meanra": 1.0, "meandec": 1.0, "lastmjd": 61352.0}
    undated = {"oid": 1998, "meanra": 1.0, "meandec": 1.0}
    stub = SchemaAlerce(lambda d: d, extra_objects=[stale, undated])
    stub.detections["1998"] = []
    with caplog.at_level(logging.WARNING):
        found = [c async for c in _broker(stub).poll(SINCE, 30.0)]
    detection_oids = {
        k["oid"] for n, k in stub.calls if n == "query_detections"
    }
    assert 1999 not in detection_oids
    assert 1998 in detection_oids
    assert {c.dia_object_id for c in found} == _expected(30.0)
    assert not any("1999" in r.getMessage() for r in caplog.records)


class RepeatingAlerce(StubAlerce):
    """Page 2 repeats page 1, as when classifier rows straddle pages."""

    @override
    def query_objects(self, **kwargs: Any) -> list[dict[str, Any]]:
        if kwargs["page"] == 2:
            kwargs = {**kwargs, "page": 1}
        return super().query_objects(**kwargs)


async def test_page_of_only_duplicates_is_not_a_schema_error() -> None:
    stub = RepeatingAlerce()
    found = [c async for c in _broker(stub).poll(SINCE, 30.0)]
    page1 = {1001, 1002, 1003, 1004}
    assert {c.dia_object_id for c in found} == _expected(30.0) & page1


class PagesAlerce(StubAlerce):
    """Serves explicit pages of objects; detections from ``extra`` first."""

    def __init__(
        self,
        pages: list[list[dict[str, Any]]],
        extra: dict[str, list[dict[str, Any]]],
        *,
        failing: set[int] | None = None,
        transform: Transform | None = None,
    ) -> None:
        super().__init__()
        self.pages = pages
        self.detections.update(extra)
        self.failing = failing or set()
        self.transform = transform

    @override
    def query_objects(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("query_objects", kwargs))
        page = kwargs["page"]
        return self.pages[page - 1] if page <= len(self.pages) else []

    @override
    def query_detections(self, **kwargs: Any) -> list[dict[str, Any]]:
        if int(kwargs["oid"]) in self.failing:
            raise requests.ConnectionError("timeout")
        dets = super().query_detections(**kwargs)
        return [self.transform(d) for d in dets] if self.transform else dets


def _page(*oids: int) -> list[dict[str, Any]]:
    objects = {
        o["oid"]: o
        for n in (1, 2)
        for o in json.loads((DATA / f"objects_page{n}.json").read_text())[
            "items"
        ]
    }
    return [objects[oid] for oid in oids]


def _det(oid: int, **values: Any) -> dict[str, Any]:
    det = {
        "oid": oid,
        "measurement_id": oid * 100,
        "mjd": 61353.5,
        "band_name": "r",
        "psfFlux": 1e6,
        "ra": 10.0,
        "dec": 10.0,
    }
    return {**det, **values}


def _obj(oid: int, **values: Any) -> dict[str, Any]:
    return {"oid": oid, "meanra": 10.0, "meandec": 10.0, **values}


async def test_null_flux_on_lone_new_object_is_not_an_outage() -> None:
    stub = PagesAlerce(
        [_page(1001, 1002, 1003, 1004), [*_page(1001, 1002), _obj(2001)]],
        {"2001": [_det(2001, psfFlux=None)]},
    )
    found = [c async for c in _broker(stub).poll(SINCE, 30.0)]
    assert {c.dia_object_id for c in found} == _expected(30.0) & {
        1001,
        1002,
        1003,
        1004,
    }


async def test_unusable_values_are_skipped_without_outage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stub = PagesAlerce(
        [
            [
                _obj(2001),
                _obj(2002),
                _obj(2003, meanra=None, meandec=None),
            ]
        ],
        {
            "2001": [_det(2001, psfFlux=float("nan"))],
            "2002": [_det(2002, psfFlux=None)],
            "2003": [_det(2003, ra=None, dec=None)],
        },
    )
    with caplog.at_level(logging.WARNING):
        found = [c async for c in _broker(stub).poll(SINCE, 30.0)]
    assert found == []
    assert any("2001" in r.getMessage() for r in caplog.records)


async def test_rename_masked_by_failed_fetches_still_raises() -> None:
    stub = PagesAlerce(
        [_page(1001, 1002, 1003, 1004), _page(1005, 1006)],
        {},
        failing={1001, 1005},
        transform=_rename("psfFlux", "psf_flux"),
    )
    with pytest.raises(BrokerUnavailableError) as e:
        [c async for c in _broker(stub).poll(SINCE, 30.0)]
    assert "psf_flux" in str(e.value)


async def test_band_code_is_not_used_as_band_name() -> None:
    stub = SchemaAlerce(_drop("band_name"), oids={"1003"})
    found = [c async for c in _broker(stub).poll(SINCE, 30.0)]
    assert 1003 not in {c.dia_object_id for c in found}
    assert all(c.band.isalpha() for c in found)
