"""A respx-based fake LCFTP server and bucket for client tests."""

import hashlib
import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
import respx

from lcftp_models.observatory import ObservatoryProfile
from lcftp_models.plan import AvailabilityWindow, ObservationPlan

BASE = "https://lcftp.example.com"
BUCKET = "https://bucket.example.com"


class FakeApi:
    """Server endpoints and a bucket, mocked with respx."""

    def __init__(
        self,
        router: respx.MockRouter,
        profile: ObservatoryProfile,
        make_plan: Callable[[AvailabilityWindow], ObservationPlan],
    ) -> None:
        self.profile = profile
        self.make_plan = make_plan
        self.down = False
        self.frames: dict[str, dict[str, Any]] = {}
        self.batches: dict[str, list[str]] = {}
        self.stored: dict[str, bytes] = {}
        self.outcomes: list[dict[str, Any]] = []
        router.get(f"{BASE}/api/device/whoami").mock(side_effect=self._whoami)
        router.post(
            url__regex=rf"{BASE}/api/observatories/.*/availability"
        ).mock(side_effect=self._availability)
        router.post(f"{BASE}/api/uploads").mock(side_effect=self._uploads)
        router.post(url__regex=rf"{BASE}/api/uploads/.*/complete").mock(
            side_effect=self._complete
        )
        router.put(url__startswith=BUCKET).mock(side_effect=self._put)

    def _check(self) -> None:
        if self.down:
            raise httpx.ConnectError("server down")

    def _whoami(self, request: httpx.Request) -> httpx.Response:
        self._check()
        return httpx.Response(200, json=self.profile.model_dump(mode="json"))

    def _availability(self, request: httpx.Request) -> httpx.Response:
        self._check()
        window = AvailabilityWindow.model_validate_json(request.content)
        plan = self.make_plan(window)
        return httpx.Response(201, json=plan.model_dump(mode="json"))

    def _uploads(self, request: httpx.Request) -> httpx.Response:
        self._check()
        manifest = json.loads(request.content)
        batch = manifest["batch_id"]
        self.outcomes.extend(manifest["outcomes"])
        uploads = []
        for frame in manifest["frames"]:
            fid = frame["frame_id"]
            self.frames[fid] = frame
            self.batches.setdefault(batch, []).append(fid)
            uploads.append(
                {
                    "frame_id": fid,
                    "url": f"{BUCKET}/{fid}",
                    "method": "PUT",
                    "headers": {
                        "x-lcftp-sha256": frame["checksums"]["sha256"]
                    },
                    "expires_at": "2099-01-01T00:00:00Z",
                }
            )
        return httpx.Response(
            200,
            json={
                "batch_id": batch,
                "uploads": uploads,
                "already_received": [],
            },
        )

    def _put(self, request: httpx.Request) -> httpx.Response:
        fid = request.url.path.lstrip("/")
        if hashlib.sha256(request.content).hexdigest() != request.headers.get(
            "x-lcftp-sha256"
        ):
            return httpx.Response(400)
        self.stored[fid] = request.content
        return httpx.Response(200)

    def _complete(self, request: httpx.Request) -> httpx.Response:
        self._check()
        batch = request.url.path.split("/")[-2]
        fids = self.batches.get(batch, [])
        received = [f for f in fids if f in self.stored]
        missing = [f for f in fids if f not in self.stored]
        return httpx.Response(
            200,
            json={
                "batch_id": batch,
                "received": received,
                "missing": missing,
                "mismatched": [],
            },
        )


def frame_ids(api: FakeApi) -> set[UUID]:
    """Return ids of frames stored in the fake bucket."""
    return {UUID(f) for f in api.stored}
