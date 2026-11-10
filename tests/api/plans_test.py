import asyncio
import threading
import time
from datetime import UTC, date, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lcftp.api import plans as plans_api
from lcftp.brokers.testing import make_candidate
from lcftp.db.schema import Assignment
from lcftp.stores.candidates import SqlCandidateStore
from lcftp.stores.plans import SqlPlanStore
from lcftp_models.plan import ObservationPlan
from lcftp_models.site import SiteLocation
from tests.support.factories import make_device, make_observatory, make_user

ALERT = datetime(2026, 11, 10, 1, tzinfo=UTC)
NIGHT = {"start": "2026-11-10T02:00:00Z", "end": "2026-11-10T12:00:00Z"}


async def _setup(
    session: AsyncSession,
    observatory_id: str = "obs1",
    site: SiteLocation | None = None,
) -> dict[str, str]:
    user = await make_user(session)
    if site:
        await make_observatory(session, user, observatory_id, site=site)
    else:
        await make_observatory(session, user, observatory_id)
    store = SqlCandidateStore(session)
    for oid, ra in ((1, 45.0), (2, 60.0), (3, 75.0)):
        await store.upsert(
            make_candidate(oid, ra=ra, dec=20, mag=12, when=ALERT)
        )
    await store.upsert(make_candidate(4, ra=50, dec=20, mag=18, when=ALERT))
    headers = await make_device(session, observatory_id)
    await session.commit()
    return headers


async def test_plan_from_candidates(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _setup(db_session)
    r = await client.post(
        "/api/observatories/obs1/availability", json=NIGHT, headers=headers
    )
    assert r.status_code == 201
    plan = ObservationPlan.model_validate(r.json())
    assert plan.observatory_id == "obs1"
    assert plan.planner_version == "1"
    assert {i.candidate.dia_object_id for i in plan.items} == {1, 2, 3}

    r = await client.get(f"/api/plans/{plan.plan_id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["superseded_by"] is None
    assert ObservationPlan.model_validate(r.json()["plan"]) == plan


async def test_overlapping_request_supersedes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _setup(db_session)
    url = "/api/observatories/obs1/availability"
    first = (await client.post(url, json=NIGHT, headers=headers)).json()
    second = (await client.post(url, json=NIGHT, headers=headers)).json()
    r = await client.get(f"/api/plans/{first['plan_id']}", headers=headers)
    assert r.json()["superseded_by"] == second["plan_id"]
    counts = await SqlPlanStore(db_session).assignment_counts(
        [date(2026, 11, 9)], [1, 2, 3]
    )
    assert counts == {1: 1, 2: 1, 3: 1}


async def test_other_observatory_forbidden(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _setup(db_session)
    r = await client.post(
        "/api/observatories/someone-else/availability",
        json=NIGHT,
        headers=headers,
    )
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"


async def test_other_observatory_plan_hidden(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _setup(db_session)
    plan = (
        await client.post(
            "/api/observatories/obs1/availability", json=NIGHT, headers=headers
        )
    ).json()
    user = await make_user(db_session, "other", 77)
    await make_observatory(db_session, user, "obs2")
    other = await make_device(db_session, "obs2")
    await db_session.commit()
    r = await client.get(f"/api/plans/{plan['plan_id']}", headers=other)
    assert r.status_code == 404


async def test_daytime_window_gives_empty_plan(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _setup(db_session)
    day = {"start": "2026-11-10T18:00:00Z", "end": "2026-11-10T22:00:00Z"}
    r = await client.post(
        "/api/observatories/obs1/availability", json=day, headers=headers
    )
    assert r.status_code == 201
    assert r.json()["items"] == []


async def test_five_minute_window_gives_empty_plan(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _setup(db_session)
    short = {"start": "2026-11-10T07:00:00Z", "end": "2026-11-10T07:05:00Z"}
    r = await client.post(
        "/api/observatories/obs1/availability", json=short, headers=headers
    )
    assert r.status_code == 201
    assert r.json()["items"] == []


async def test_mauna_kea_night_assignment(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    mauna_kea = SiteLocation(
        latitude=19.82,
        longitude=-155.47,
        elevation_m=4200,
        timezone="Pacific/Honolulu",
    )
    headers = await _setup(db_session, "mk", mauna_kea)
    window = {"start": "2026-11-10T05:00:00Z", "end": "2026-11-10T15:00:00Z"}
    r = await client.post(
        "/api/observatories/mk/availability", json=window, headers=headers
    )
    assert r.status_code == 201
    assert r.json()["items"]
    nights = (
        (await db_session.execute(select(Assignment.night))).scalars().all()
    )
    assert set(nights) == {date(2026, 11, 9)}


async def test_naive_datetimes_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _setup(db_session)
    naive = {"start": "2026-11-10T02:00:00", "end": "2026-11-10T12:00:00"}
    r = await client.post(
        "/api/observatories/obs1/availability", json=naive, headers=headers
    )
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_request"


async def test_night_follows_items_not_window_start(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    mauna_kea = SiteLocation(
        latitude=19.82,
        longitude=-155.47,
        elevation_m=4200,
        timezone="Pacific/Honolulu",
    )
    headers = await _setup(db_session, "mk", mauna_kea)
    # Starts 10:00 HST on the 10th; every target is observed that evening.
    window = {"start": "2026-11-10T20:00:00Z", "end": "2026-11-11T20:00:00Z"}
    r = await client.post(
        "/api/observatories/mk/availability", json=window, headers=headers
    )
    assert r.json()["items"]
    nights = (
        (await db_session.execute(select(Assignment.night))).scalars().all()
    )
    assert set(nights) == {date(2026, 11, 10)}
    counts = await SqlPlanStore(db_session).assignment_counts(
        [date(2026, 11, 10)], [1, 2, 3]
    )
    assert counts == {1: 1, 2: 1, 3: 1}


async def test_planning_does_not_block_event_loop(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = await _setup(db_session)
    started = threading.Event()
    finished: list[float] = []

    def slow_planner(*args: object) -> list[object]:
        started.set()
        time.sleep(0.5)
        finished.append(time.monotonic())
        return []

    monkeypatch.setattr(plans_api, "plan_observations", slow_planner)
    plan = asyncio.create_task(
        client.post(
            "/api/observatories/obs1/availability", json=NIGHT, headers=headers
        )
    )
    await asyncio.to_thread(started.wait, 5)
    r = await client.get("/health")
    health_done = time.monotonic()
    assert r.status_code == 200
    assert (await plan).status_code == 201
    assert health_done < finished[0]
