from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lcftp.brokers.testing import make_candidate
from lcftp.db.schema import OutcomeRow
from lcftp.storage.memory import MemoryObjectStore
from lcftp.stores.plans import SqlPlanStore
from lcftp_models.checksums import compute_checksums
from lcftp_models.plan import (
    AvailabilityWindow,
    ExposureHint,
    ObservationPlan,
    PlanItem,
)
from lcftp_models.upload import (
    FrameRecord,
    ItemOutcome,
    OutcomeReason,
    UploadManifest,
    UploadResult,
    UploadTicket,
)
from tests.support.factories import make_device, make_observatory, make_user

T0 = datetime(2026, 11, 10, 3, tzinfo=UTC)


async def _plan(session: AsyncSession, observatory_id: str = "obs1") -> None:
    candidate = make_candidate(123, when=T0)
    plan = ObservationPlan(
        plan_id=f"plan-{observatory_id}",
        observatory_id=observatory_id,
        window=AvailabilityWindow(start=T0, end=T0 + timedelta(hours=8)),
        created_at=T0,
        candidates_as_of=None,
        planner_version="1",
        items=[
            PlanItem(
                item_id="000",
                candidate=candidate,
                not_before=T0,
                not_after=T0 + timedelta(minutes=10),
                suggested_filter=None,
                exposure_hint=ExposureHint(total_exposure_s=300),
                priority=0,
            )
        ],
    )
    await SqlPlanStore(session).save(plan, "America/Phoenix")


@pytest.fixture
async def headers(db_session: AsyncSession) -> dict[str, str]:
    user = await make_user(db_session)
    await make_observatory(db_session, user)
    await _plan(db_session)
    headers = await make_device(db_session)
    await db_session.commit()
    return headers


def _frame(
    tmp_path: Path,
    data: bytes = b"SIMPLE  = T",
    *,
    item_id: str = "000",
    observatory_id: str = "obs1",
) -> tuple[FrameRecord, bytes]:
    path = tmp_path / f"{uuid4()}.fits"
    path.write_bytes(data)
    record = FrameRecord(
        frame_id=uuid4(),
        plan_id="plan-obs1",
        item_id=item_id,
        observatory_id=observatory_id,
        dia_object_id=123,
        alert_id="12300",
        obs_start=T0,
        exposure_s=10,
        filter="r",
        driver="simulated",
        driver_version="1",
        ra=30,
        dec=0,
        size_bytes=len(data),
        checksums=compute_checksums(path),
    )
    return record, data


def _store(app: FastAPI) -> MemoryObjectStore:
    store = app.state.store
    assert isinstance(store, MemoryObjectStore)
    return store


async def _put(
    app: FastAPI, ticket: UploadTicket, data: dict[UUID, bytes]
) -> None:
    transport = httpx.ASGITransport(app=_store(app).asgi_app)
    async with httpx.AsyncClient(transport=transport) as bucket:
        for upload in ticket.uploads:
            r = await bucket.put(
                upload.url,
                content=data[upload.frame_id],
                headers=upload.headers,
            )
            assert r.status_code == 200


def _manifest(
    *frames: FrameRecord, outcomes: list[ItemOutcome] | None = None
) -> dict:
    manifest = UploadManifest(
        batch_id=uuid4(), frames=list(frames), outcomes=outcomes or []
    )
    return manifest.model_dump(mode="json")


async def test_happy_path(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], tmp_path: Path
) -> None:
    record, data = _frame(tmp_path)
    body = _manifest(record)
    r = await client.post("/api/uploads", json=body, headers=headers)
    assert r.status_code == 200, r.text
    ticket = UploadTicket.model_validate(r.json())
    assert [u.frame_id for u in ticket.uploads] == [record.frame_id]
    assert ticket.already_received == []
    await _put(app, ticket, {record.frame_id: data})

    r = await client.post(
        f"/api/uploads/{body['batch_id']}/complete", headers=headers
    )
    result = UploadResult.model_validate(r.json())
    assert result.received == [record.frame_id]
    key = f"alerts/123/obs1/plan-obs1/{record.frame_id}.fits"
    assert _store(app).objects[key] == data


async def test_other_observatory_forbidden(
    client: AsyncClient, headers: dict[str, str], tmp_path: Path
) -> None:
    record, _ = _frame(tmp_path, observatory_id="obs2")
    r = await client.post(
        "/api/uploads", json=_manifest(record), headers=headers
    )
    assert r.status_code == 403


async def test_unknown_item(
    client: AsyncClient, headers: dict[str, str], tmp_path: Path
) -> None:
    record, _ = _frame(tmp_path, item_id="999")
    r = await client.post(
        "/api/uploads", json=_manifest(record), headers=headers
    )
    assert r.status_code == 422
    assert r.json()["code"] == "frame_not_in_plan"


async def test_oversize_frame(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], tmp_path: Path
) -> None:
    record, _ = _frame(tmp_path)
    big = record.model_copy(update={"size_bytes": 200 * 1024**2 + 1})
    r = await client.post("/api/uploads", json=_manifest(big), headers=headers)
    assert r.status_code == 422
    assert r.json()["code"] == "frame_too_large"


async def test_missing_and_mismatched(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], tmp_path: Path
) -> None:
    missing, _ = _frame(tmp_path, b"one")
    tampered, _ = _frame(tmp_path, b"two")
    body = _manifest(missing, tampered)
    r = await client.post("/api/uploads", json=body, headers=headers)
    assert r.status_code == 200
    _store(app).put(
        f"alerts/123/obs1/plan-obs1/{tampered.frame_id}.fits", b"evil"
    )
    r = await client.post(
        f"/api/uploads/{body['batch_id']}/complete", headers=headers
    )
    result = UploadResult.model_validate(r.json())
    assert result.missing == [missing.frame_id]
    assert result.mismatched == [tampered.frame_id]
    assert result.received == []


async def test_outcomes_stored_once(
    client: AsyncClient,
    headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    outcome = ItemOutcome(
        plan_id="plan-obs1",
        item_id="000",
        status="skipped",
        reason=OutcomeReason.weather,
        started_at=None,
        finished_at=None,
    )
    body = _manifest(outcomes=[outcome])
    for _ in range(2):
        r = await client.post("/api/uploads", json=body, headers=headers)
        assert r.status_code == 200
    rows = (await db_session.execute(select(OutcomeRow))).scalars().all()
    assert [(r.item_id, r.reason) for r in rows] == [("000", "weather")]


async def test_repost_after_completion_is_idempotent(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], tmp_path: Path
) -> None:
    record, data = _frame(tmp_path)
    body = _manifest(record)
    ticket = UploadTicket.model_validate(
        (await client.post("/api/uploads", json=body, headers=headers)).json()
    )
    await _put(app, ticket, {record.frame_id: data})
    url = f"/api/uploads/{body['batch_id']}/complete"
    first = (await client.post(url, headers=headers)).json()

    r = await client.post("/api/uploads", json=body, headers=headers)
    again = UploadTicket.model_validate(r.json())
    assert again.uploads == []
    assert again.already_received == [record.frame_id]
    assert (await client.post(url, headers=headers)).json() == first


async def test_repost_resigns_pending(
    client: AsyncClient, headers: dict[str, str], tmp_path: Path
) -> None:
    record, _ = _frame(tmp_path)
    body = _manifest(record)
    await client.post("/api/uploads", json=body, headers=headers)
    r = await client.post("/api/uploads", json=body, headers=headers)
    assert [u["frame_id"] for u in r.json()["uploads"]] == [
        str(record.frame_id)
    ]


async def test_complete_unknown_batch(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    r = await client.post(f"/api/uploads/{uuid4()}/complete", headers=headers)
    assert r.status_code == 404
