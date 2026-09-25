from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from canvas_helper.config import Settings
from canvas_helper.db import init_db, make_engine, make_session_factory
from canvas_helper.job_queue import SyncJobQueue, adaptive_interval, utcnow
from canvas_helper.main import create_app
from canvas_helper.models import LOCAL_USER_ID, SyncJob, SyncSchedule, User


@pytest.mark.asyncio
async def test_queue_deduplicates_and_claims_users_fairly():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    sessions = make_session_factory(engine)
    async with sessions() as session:
        session.add(User(id="other-user", display_name="Other"))
        await session.commit()

    async def unused_canvas(_user_id: str):
        raise AssertionError("execution is not part of this test")

    queue = SyncJobQueue(sessions, unused_canvas)
    first, created = await queue.enqueue(LOCAL_USER_ID, "courses")
    duplicate, duplicate_created = await queue.enqueue(LOCAL_USER_ID, "courses")
    await queue.enqueue(LOCAL_USER_ID, "planner")
    other, _ = await queue.enqueue("other-user", "courses")
    assert created is True
    assert duplicate_created is False
    assert duplicate.id == first.id

    claimed_first = await queue.claim()
    claimed_second = await queue.claim()
    assert claimed_first is not None
    assert claimed_second is not None
    assert {claimed_first.user_id, claimed_second.user_id} == {
        LOCAL_USER_ID,
        "other-user",
    }
    assert claimed_second.id == other.id or claimed_first.id == other.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_running_job_is_recovered_and_can_be_cancelled():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    sessions = make_session_factory(engine)

    async def unused_canvas(_user_id: str):
        raise AssertionError

    queue = SyncJobQueue(sessions, unused_canvas)
    row, _ = await queue.enqueue(LOCAL_USER_ID, "planner")
    async with sessions() as session:
        stored = await session.get(SyncJob, row.id)
        assert stored is not None
        stored.status = "running"
        stored.attempts = 1
        stored.lease_owner = "dead-worker"
        stored.lease_expires_at = utcnow() - timedelta(seconds=1)
        await session.commit()
    assert await queue.recover_stale() == 1
    cancelled = await queue.cancel(LOCAL_USER_ID, row.id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.dedupe_key is None
    await engine.dispose()


def test_adaptive_schedule_backs_off_for_low_rate_limit():
    normal = adaptive_interval("planner", change_count=2, rate_remaining=700)
    quiet = adaptive_interval("planner", change_count=0, rate_remaining=700)
    constrained = adaptive_interval("planner", change_count=2, rate_remaining=50)
    busy = adaptive_interval("planner", change_count=30, rate_remaining=700)
    assert normal == 300
    assert quiet > normal
    assert constrained > normal
    assert busy < normal


@pytest.mark.asyncio
async def test_automatic_schedules_are_initialized_for_connected_user():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    sessions = make_session_factory(engine)

    async def unused_canvas(_user_id: str):
        raise AssertionError

    queue = SyncJobQueue(sessions, unused_canvas)
    assert await queue.ensure_schedules(LOCAL_USER_ID) == 5
    assert await queue.ensure_schedules(LOCAL_USER_ID) == 0
    async with sessions() as session:
        schedules = list(
            (await session.scalars(select(SyncSchedule))).all()
        )
    assert {row.job for row in schedules} == {
        "announcements",
        "planner",
        "deadlines",
        "materials",
        "courses",
    }
    assert all(row.next_run_at > row.updated_at for row in schedules)
    await engine.dispose()


def test_sync_api_returns_202_job_and_waits_deterministically(tmp_path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/users/self/profile":
            return httpx.Response(200, json={"id": 7, "name": "Test User"})
        if request.url.path == "/api/v1/courses":
            return httpx.Response(
                200,
                json=[{"id": 1, "name": "Course", "course_code": "C1"}],
            )
        return httpx.Response(404)

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
        allow_development_secret_file=True,
    )
    app = create_app(settings, canvas_transport=httpx.MockTransport(handler))
    headers = {"X-Canvas-Helper": "1", "Origin": "http://testserver"}
    with TestClient(app) as client:
        setup = client.post(
            "/api/setup/token",
            json={
                "canvas_url": "https://canvas.example.edu",
                "token": "secret-value",
            },
            headers=headers,
        )
        assert setup.status_code == 200
        response = client.post("/api/sync/courses?wait=true", headers=headers)
        assert response.status_code == 202
        payload = response.json()
        assert payload["job_id"]
        assert payload["job"]["status"] == "success"
        status = client.get("/api/sync/status").json()
        assert status["last_success"]["id"] == payload["job_id"]
        assert status["running"] == []
