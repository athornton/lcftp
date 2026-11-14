#!/usr/bin/env python3
"""Record live ALeRCE LSST responses as replayable test fixtures.

Needs network access to ``api-lsst.alerce.online``. Writes files in the
shapes ``tests/brokers/alerce_test.py``'s stub client replays::

    uv run python scripts/record-alerce-fixtures.py \
        --out tests/data/alerce/recorded
"""

import argparse
import json
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any, Protocol

from lcftp.brokers.alerce import AlerceClient, datetime_to_mjd

_SURVEY = "lsst"


class RecorderClient(AlerceClient, Protocol):
    """The broker's client protocol plus the single-object query."""

    def query_object(self, **kwargs: Any) -> Any:
        """Query one object's full record."""


DEFAULT_OUT = (
    Path(__file__).parent.parent / "tests" / "data" / "alerce" / "recorded"
)


def _write(path: Path, data: Any) -> None:
    text = json.dumps(data, indent=1, sort_keys=True, default=str)
    path.write_text(text + "\n")


def record(
    client: RecorderClient,
    out_dir: Path,
    *,
    now_mjd: float,
    window_days: float = 1.0,
    page_size: int = 10,
    pages: int = 2,
) -> dict[str, Any]:
    """Record object pages, their detections and one full object.

    Paging stops after the first empty page, which is still written so a
    replay ends at the same place.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    window = [now_mjd - window_days, now_mjd]
    objects: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        items = client.query_objects(
            survey=_SURVEY,
            format="json",
            lastmjd=window,
            order_by="lastmjd",
            order_mode="DESC",
            page=page,
            page_size=page_size,
        )
        _write(out_dir / f"objects_page{page}.json", {"items": items})
        if not items:
            break
        objects.extend(items)
    detections = {
        str(o["oid"]): client.query_detections(
            survey=_SURVEY, oid=o["oid"], format="json"
        )
        for o in objects
    }
    _write(out_dir / "detections.json", {"detections": detections})
    if objects:
        obj = client.query_object(
            survey=_SURVEY, oid=objects[0]["oid"], format="json"
        )
        _write(out_dir / "object.json", obj)
    meta = {
        "recorded_at": datetime.now(tz=UTC).isoformat(),
        "alerce_version": version("alerce"),
        "window": window,
        "page_size": page_size,
        "pages": pages,
    }
    _write(out_dir / "meta.json", meta)
    return meta


def main() -> None:
    """Record fixtures from the live ALeRCE API."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--pages", type=int, default=2)
    parser.add_argument("--page-size", type=int, default=10)
    parser.add_argument("--window-days", type=float, default=1.0)
    args = parser.parse_args()

    from alerce.core import Alerce  # noqa: PLC0415

    meta = record(
        Alerce(),
        args.out,
        now_mjd=datetime_to_mjd(datetime.now(tz=UTC)),
        window_days=args.window_days,
        page_size=args.page_size,
        pages=args.pages,
    )
    print(json.dumps(meta, indent=1))  # noqa: T201


if __name__ == "__main__":
    main()
