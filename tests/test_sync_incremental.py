"""Steady-state resyncs must not rewrite work that has not changed."""

import httpx
import pytest
from sqlalchemy import select

from canvas_helper.canvas.client import CanvasClient
from canvas_helper.db import init_db, make_engine, make_session_factory
from canvas_helper.models import Assignment, Course, DocChunk, Document, LOCAL_USER_ID
from canvas_helper.sync import SyncService


def page_handler(body: str):
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
                    }
                ],
            )
        if path.endswith("/pages/welcome"):
            return httpx.Response(
                200,
                json={
                    "title": "Welcome",
                    "html_url": "https://canvas.example.edu/courses/1/pages/welcome",
                    "body": body,
                },
            )
        if path.endswith("/pages"):
            return httpx.Response(404, json={"message": "disabled"})
        return httpx.Response(404)

    return handler


async def run_sync(sessions, handler) -> dict:
    client = CanvasClient(
        "https://canvas.example.edu",
        "token-value",
        transport=httpx.MockTransport(handler),
        max_retries=0,
    )
    try:
        return await SyncService(sessions, client, LOCAL_USER_ID).sync_materials()
    finally:
        await client.close()


async def chunk_rows(sessions, title: str) -> list[tuple[int, str]]:
    """Chunk identity and text. SQLite reuses rowids after a delete, so the id
    alone cannot tell a rebuild from an untouched row."""
    async with sessions() as session:
        document = await session.scalar(
            select(Document).where(Document.title == title)
        )
        assert document is not None
        rows = (
            await session.execute(
                select(DocChunk.id, DocChunk.content)
                .where(DocChunk.document_id == document.id)
                .order_by(DocChunk.ordinal)
            )
        ).all()
        return [(row[0], row[1]) for row in rows]


@pytest.mark.asyncio
async def test_unchanged_sources_keep_their_existing_chunks():
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

    handler = page_handler("<p>Hello <strong>students</strong></p>")
    first = await run_sync(sessions, handler)
    assert first["changed"] == 2
    original = await chunk_rows(sessions, "Welcome")
    assert original

    second = await run_sync(sessions, handler)
    # Nothing moved on the Canvas side, so nothing should have been reindexed.
    assert second["changed"] == 0
    assert await chunk_rows(sessions, "Welcome") == original
    await engine.dispose()


@pytest.mark.asyncio
async def test_edited_sources_are_reindexed():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    sessions = make_session_factory(engine)
    async with sessions() as session:
        session.add(Course(id=1, canvas_id=1, name="Course", raw={}))
        await session.commit()

    await run_sync(sessions, page_handler("<p>First revision</p>"))
    original = await chunk_rows(sessions, "Welcome")

    result = await run_sync(sessions, page_handler("<p>Second revision entirely</p>"))
    assert result["changed"] == 1
    refreshed = await chunk_rows(sessions, "Welcome")
    assert [text for _, text in refreshed] != [text for _, text in original]
    async with sessions() as session:
        document = await session.scalar(
            select(Document).where(Document.title == "Welcome")
        )
        assert "Second revision" in (document.content or "")
    await engine.dispose()
