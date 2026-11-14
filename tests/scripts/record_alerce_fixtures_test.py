import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

SCRIPT = (
    Path(__file__).parent.parent.parent
    / "scripts"
    / "record-alerce-fixtures.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("record_alerce", SCRIPT)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OBJECTS = [
    {"oid": 11, "meanra": 1.0, "meandec": 2.0, "lastmjd": 61353.5},
    {"oid": 12, "meanra": 3.0, "meandec": 4.0, "lastmjd": 61353.6},
]


class StubClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def query_objects(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("query_objects", kwargs))
        return OBJECTS if kwargs["page"] == 1 else []

    def query_detections(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("query_detections", kwargs))
        return [{"oid": kwargs["oid"], "mjd": 61353.5, "psfFlux": 1.0}]

    def query_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("query_object", kwargs))
        return {"oid": kwargs["oid"], "diaObjectId": kwargs["oid"]}


def _read(path: Path) -> Any:
    return json.loads(path.read_text())


def test_record_writes_replayable_fixtures(tmp_path: Path) -> None:
    stub = StubClient()
    meta = _load().record(stub, tmp_path, now_mjd=61354.0, pages=3)

    assert _read(tmp_path / "meta.json") == meta
    assert meta["window"] == [61353.0, 61354.0]
    assert meta["page_size"] == 10
    assert _read(tmp_path / "objects_page1.json")["items"] == OBJECTS
    assert _read(tmp_path / "objects_page2.json")["items"] == []
    assert not (tmp_path / "objects_page3.json").exists()
    assert set(_read(tmp_path / "detections.json")["detections"]) == {
        "11",
        "12",
    }
    assert _read(tmp_path / "object.json")["oid"] == 11

    pages = [k["page"] for n, k in stub.calls if n == "query_objects"]
    assert pages == [1, 2]
    assert all(k["survey"] == "lsst" for _, k in stub.calls)
    first = next(k for n, k in stub.calls if n == "query_objects")
    assert first["lastmjd"] == [61353.0, 61354.0]
    assert first["order_by"] == "lastmjd"


def test_recorded_json_is_sorted_and_indented(tmp_path: Path) -> None:
    _load().record(StubClient(), tmp_path, now_mjd=61354.0)
    text = (tmp_path / "object.json").read_text()
    assert (
        text
        == json.dumps({"diaObjectId": 11, "oid": 11}, indent=1, sort_keys=True)
        + "\n"
    )
