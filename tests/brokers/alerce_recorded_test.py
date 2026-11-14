"""Replay tests against responses recorded from the live ALeRCE LSST API."""

import json
from collections import Counter
from typing import Any

import pytest

from lcftp.brokers.alerce import (
    DETECTION_FIELDS,
    OBJECT_FIELDS,
    AlerceBroker,
    MissingFieldError,
    mjd_to_datetime,
    resolve_field,
)
from lcftp.brokers.testing import assert_broker_contract
from tests.support.alerce import DATA, StubAlerce

RECORDED = DATA / "recorded"


def _load(name: str) -> Any:
    return json.loads((RECORDED / name).read_text())


META = _load("meta.json")
EXPECTED = _load("expected.json")
SINCE = mjd_to_datetime(META["window"][0])


def _objects() -> list[dict[str, Any]]:
    return [
        o
        for path in sorted(RECORDED.glob("objects_page*.json"))
        for o in json.loads(path.read_text())["items"]
    ]


def _detections() -> dict[str, list[dict[str, Any]]]:
    return _load("detections.json")["detections"]


def _broker(stub: StubAlerce) -> AlerceBroker:
    return AlerceBroker(stub, page_delay_s=0)


def _check(record: dict[str, Any], fields: Any, logical: str) -> None:
    try:
        resolve_field(record, fields, logical)
    except MissingFieldError as e:
        pytest.fail(str(e))


def test_required_fields_resolve_on_every_recorded_record() -> None:
    objects = _objects()
    assert objects
    for obj in objects:
        _check(obj, OBJECT_FIELDS, "oid")
        for det in _detections()[str(obj["oid"])]:
            for logical in ("mjd", "flux", "band"):
                _check(det, DETECTION_FIELDS, logical)
            for axis in ("ra", "dec"):
                if resolve_field(det, DETECTION_FIELDS, axis, required=False):
                    continue
                _check(obj, OBJECT_FIELDS, axis)


async def test_recorded_golden_candidate() -> None:
    stub = StubAlerce(data=RECORDED)
    found = {c.dia_object_id: c async for c in _broker(stub).poll(SINCE, 40.0)}
    c = found[EXPECTED["oid"]]
    assert c.ra == EXPECTED["ra"]
    assert c.dec == EXPECTED["dec"]
    assert c.band == EXPECTED["band"]
    assert c.latest_alert_id == EXPECTED["alert_id"]
    assert c.latest_alert_time == mjd_to_datetime(EXPECTED["mjd"])
    assert c.mag == pytest.approx(EXPECTED["mag"], abs=1e-6)
    assert c.sources[0].broker_object_id == str(EXPECTED["oid"])
    assert c.classification == EXPECTED["classification"]
    assert c.score == pytest.approx(EXPECTED["score"])


async def test_recorded_bogus_objects_are_dropped() -> None:
    bogus = {o["oid"] for o in _objects() if o["class_name"] == "bogus"}
    assert bogus, "recording no longer has bogus objects"
    stub = StubAlerce(data=RECORDED)
    found = {c.dia_object_id async for c in _broker(stub).poll(SINCE, 40.0)}
    assert not found & bogus


async def test_recorded_replay_passes_broker_contract() -> None:
    found = await assert_broker_contract(
        _broker(StubAlerce(data=RECORDED)), since=SINCE, mag_limit=40.0
    )
    assert found


async def test_duplicate_object_rows_fetch_detections_once() -> None:
    oids = Counter(o["oid"] for o in _objects())
    assert max(oids.values()) > 1, "recording no longer has duplicate rows"
    stub = StubAlerce(data=RECORDED)
    found = [c async for c in _broker(stub).poll(SINCE, 40.0)]
    fetched = Counter(
        k["oid"] for n, k in stub.calls if n == "query_detections"
    )
    bogus = {o["oid"] for o in _objects() if o["class_name"] == "bogus"}
    assert set(fetched) == set(oids) - bogus
    assert max(fetched.values()) == 1
    ids = [c.dia_object_id for c in found]
    assert len(ids) == len(set(ids))


def test_recorded_objects_fall_in_window() -> None:
    start, end = META["window"]
    for obj in _objects():
        assert start <= obj["lastmjd"] <= end, obj["oid"]


def test_oid_matches_dia_object_id() -> None:
    for oid, dets in _detections().items():
        for det in dets:
            assert det["diaObjectId"] == int(oid)
