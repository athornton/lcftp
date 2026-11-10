"""Live checks for the ALeRCE LSST spike (needs network access).

Run with ``uv run --with alerce python docs/superpowers/notes/alerce_spike.py``
and paste the output into ``alerce-lsst-spike.md``.
"""

import json
import time

from astropy.time import Time

from alerce.core import Alerce


def main() -> None:
    client = Alerce()
    now = Time.now().mjd
    start = time.monotonic()
    objects = client.query_objects(
        survey="lsst",
        format="json",
        lastmjd=[now - 1, now],
        order_by="lastmjd",
        order_mode="DESC",
        page=1,
        page_size=20,
    )
    print("objects:", len(objects), "in", time.monotonic() - start, "s")
    print(json.dumps(objects[:2], indent=2, default=str))
    oid = objects[0]["oid"]
    detections = client.query_detections(survey="lsst", oid=oid, format="json")
    print("detections:", len(detections))
    print(json.dumps(detections[:2], indent=2, default=str))


if __name__ == "__main__":
    main()
