from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
import respx

from lcftp_client.api import LcftpApiError, LcftpAuthError, ServerClient
from lcftp_models.errors import ErrorCode
from lcftp_models.plan import AvailabilityWindow
from lcftp_models.upload import UploadManifest

BASE = "https://lcftp.example.com"
T0 = datetime(2026, 11, 10, 3, tzinfo=UTC)
WINDOW = AvailabilityWindow(start=T0, end=T0 + timedelta(hours=8))
PLAN: dict[str, Any] = {
    "plan_id": "p1",
    "observatory_id": "obs1",
    "window": WINDOW.model_dump(mode="json"),
    "created_at": "2026-11-10T03:00:00Z",
    "candidates_as_of": None,
    "planner_version": "1",
    "items": [],
}
PROFILE = {
    "id": "obs1",
    "owner": "athornton",
    "name": "Backyard",
    "site": {
        "latitude": 32.23,
        "longitude": -110.95,
        "elevation_m": 728,
        "timezone": "America/Phoenix",
    },
    "aperture_m": 0.05,
    "limiting_mag": 15,
    "fov_arcmin": 77,
}


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


async def test_whoami_and_plan(api: respx.MockRouter) -> None:
    api.get("/api/device/whoami").respond(json=PROFILE)
    route = api.post("/api/observatories/obs1/availability").respond(
        201, json=PLAN
    )
    client = ServerClient(BASE, "lcftp_tok")
    try:
        profile = await client.whoami()
        assert profile.id == "obs1"
        plan = await client.request_plan("obs1", WINDOW)
        assert plan.plan_id == "p1"
    finally:
        await client.aclose()
    assert (
        route.calls.last.request.headers["authorization"] == "Bearer lcftp_tok"
    )
    for call in api.calls:
        assert call.request.headers["authorization"] == "Bearer lcftp_tok"


async def test_get_plan(api: respx.MockRouter) -> None:
    api.get("/api/plans/p1").respond(
        json={"plan": PLAN, "superseded_by": "p2"}
    )
    client = ServerClient(BASE, "t")
    status = await client.get_plan("p1")
    assert status.superseded_by == "p2"


async def test_upload_calls(api: respx.MockRouter) -> None:
    batch = uuid4()
    api.post("/api/uploads").respond(
        json={"batch_id": str(batch), "uploads": [], "already_received": []}
    )
    api.post(f"/api/uploads/{batch}/complete").respond(
        json={
            "batch_id": str(batch),
            "received": [],
            "missing": [],
            "mismatched": [],
        }
    )
    client = ServerClient(BASE, "t")
    ticket = await client.create_upload(
        UploadManifest(batch_id=batch, frames=[])
    )
    assert ticket.batch_id == batch
    result = await client.complete_upload(batch)
    assert result.batch_id == batch


async def test_auth_error(api: respx.MockRouter) -> None:
    api.get("/api/device/whoami").respond(
        401, json={"code": "not_authenticated", "message": "Token revoked"}
    )
    client = ServerClient(BASE, "t")
    with pytest.raises(LcftpAuthError, match="Token revoked"):
        await client.whoami()


async def test_api_error(api: respx.MockRouter) -> None:
    api.post("/api/uploads").respond(
        422, json={"code": "frame_not_in_plan", "message": "nope"}
    )
    client = ServerClient(BASE, "t")
    with pytest.raises(LcftpApiError) as excinfo:
        await client.create_upload(UploadManifest(batch_id=uuid4(), frames=[]))
    assert excinfo.value.code == ErrorCode.frame_not_in_plan
    assert excinfo.value.status == 422
    assert not isinstance(excinfo.value, LcftpAuthError)


async def test_non_json_error(api: respx.MockRouter) -> None:
    api.get("/api/device/whoami").respond(502, text="Bad Gateway")
    client = ServerClient(BASE, "t")
    with pytest.raises(LcftpApiError) as excinfo:
        await client.whoami()
    assert excinfo.value.status == 502
    assert excinfo.value.code == ErrorCode.internal
