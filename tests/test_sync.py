import httpx
import pytest
from sqlalchemy import func, select

from canvas_helper.canvas.client import CanvasClient
from canvas_helper.db import init_db, make_engine, make_session_factory
from canvas_helper.main import assignment_completed, planner_completed
from canvas_helper.models import (
    Announcement,
    Assignment,
    Course,
    Document,
    LOCAL_USER_ID,
    PlannerItem,
)
from canvas_helper.sync import SyncService


def test_completion_uses_canvas_submission_state():
    assignment = Assignment(
        id=1,
        canvas_id=1,
        course_id=1,
        name="Done",
        raw={"submission": {"workflow_state": "graded"}},
    )
    planner = PlannerItem(
        canvas_id="assignment:1",
        title="Done",
        completed=False,
        raw={"submissions": {"submitted": True}},
    )
    assert assignment_completed(assignment) is True
    assert planner_completed(planner) is True


@pytest.mark.asyncio
async def test_material_sync_indexes_assignments_modules_and_pages():
    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/modules"):
            return httpx.Response(200, json=[{"id": 10, "name": "Week 1"}])
        if path.endswith("/modules/10/items"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 11,
                        "type": "Page",
                        "title": "Welcome",
                        "page_url": "welcome",
                        "html_url": "https://canvas.example.edu/courses/1/pages/welcome",
                    },
                    {
                        "id": 12,
                        "type": "ExternalUrl",
                        "title": "Reading",
                        "external_url": "https://example.edu/reading",
                    },
                ],
            )
        if path.endswith("/pages/welcome"):
            return httpx.Response(
                200,
                json={
                    "title": "Welcome",
                    "html_url": "https://canvas.example.edu/courses/1/pages/welcome",
                    "body": "<p>Hello <strong>students</strong></p>",
                },
            )
        if path.endswith("/pages"):
            return httpx.Response(404, json={"message": "disabled"})
        return httpx.Response(404)

    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    sessions = make_session_factory(engine)
    async with sessions() as session:
        session.add(Course(id=1, canvas_id=1, name="Course", raw={}))
        session.add(
            Assignment(
                id=20,
                canvas_id=20,
                course_id=1,
                name="Project",
                html_url="https://canvas.example.edu/courses/1/assignments/20",
                description_html="<p>Build it</p>",
                raw={},
            )
        )
        await session.commit()

    client = CanvasClient(
        "https://canvas.example.edu",
        "token-value",
        transport=httpx.MockTransport(handler),
        max_retries=0,
    )
    try:
        result = await SyncService(sessions, client, LOCAL_USER_ID).sync_materials()
    finally:
        await client.close()

    assert result["count"] == 3
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Document)) == 3
        welcome = await session.scalar(
            select(Document).where(Document.title == "Welcome")
        )
        assert welcome is not None
        assert "Hello" in welcome.content
    await engine.dispose()


@pytest.mark.asyncio
async def test_announcement_sync_sanitizes_and_assigns_course():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/announcements"
        assert "course_1" in request.url.params.get_list("context_codes[]")
        return httpx.Response(
            200,
            json=[
                {
                    "id": 99,
                    "context_code": "course_1",
                    "title": "Week update",
                    "message": "<p>Read this</p><script>alert(1)</script>",
                    "posted_at": "2026-09-23T01:00:00Z",
                    "author": {"display_name": "Teacher"},
                    "read_state": "unread",
                    "html_url": "https://canvas.example.edu/courses/1/discussion_topics/99",
                }
            ],
        )

    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    sessions = make_session_factory(engine)
    async with sessions() as session:
        session.add(Course(id=1, canvas_id=1, name="Course", raw={}))
        await session.commit()
    client = CanvasClient(
        "https://canvas.example.edu",
        "token-value",
        transport=httpx.MockTransport(handler),
        max_retries=0,
    )
    try:
        result = await SyncService(
            sessions, client, LOCAL_USER_ID
        ).sync_announcements()
    finally:
        await client.close()
    assert result == {"count": 1, "changed": 1}
    async with sessions() as session:
        row = await session.scalar(
            select(Announcement).where(Announcement.canvas_id == 99)
        )
        assert row is not None
        assert row.course_id == 1
        assert row.author_name == "Teacher"
        assert "<script" not in row.message_html
    await engine.dispose()
