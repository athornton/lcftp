"""A stub ``alerce.core.Alerce`` that replays fixture files."""

import json
from pathlib import Path
from typing import Any

__all__ = ["DATA", "StubAlerce"]

DATA = Path(__file__).parent.parent / "data" / "alerce"


class StubAlerce:
    """Replays fixtures from ``data`` in place of ``alerce.core.Alerce``."""

    def __init__(
        self, error: Exception | None = None, *, data: Path = DATA
    ) -> None:
        self.error = error
        self.data = data
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.detections = json.loads((data / "detections.json").read_text())[
            "detections"
        ]

    def query_objects(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("query_objects", kwargs))
        if self.error:
            raise self.error
        path = self.data / f"objects_page{kwargs['page']}.json"
        return json.loads(path.read_text())["items"] if path.exists() else []

    def query_detections(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("query_detections", kwargs))
        return self.detections[str(kwargs["oid"])]
