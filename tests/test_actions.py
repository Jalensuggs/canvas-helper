import pytest

from canvas_helper.actions import ActionError, ActionService
from canvas_helper.db import init_db, make_engine, make_session_factory
from canvas_helper.models import ActionPreview, LOCAL_USER_ID, PlannerItem


class RejectingCanvas:
    async def request(self, *args, **kwargs):
        class Response:
            def json(self):
                return {"marked_complete": False}

        return Response()


class PlannerNoteCanvas:
    def __init__(self, *, mismatch: bool = False):
        self.calls = []
        self.mismatch = mismatch

    async def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))

        class Response:
            def json(self):
                return {"id": 44}

        return Response()

    async def get_json(self, path, **params):
        self.calls.append(("GET", path, {"params": params}))
        return {
            "id": 44,
            "title": "Wrong" if self.mismatch else "Review chapter",
            "details": "Pages 1-10",
            "todo_date": "2026-09-25T09:00:00Z",
        }


@pytest.mark.asyncio
async def test_action_confirmation_token_is_one_time_and_not_stored_plaintext():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    sessions = make_session_factory(engine)
    async with sessions() as session:
        item = PlannerItem(
            canvas_id="assignment:123",
            title="Read week one",
            completed=False,
            read=False,
            raw={},
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)
        item_id = item.id

    service = ActionService(sessions, ttl_seconds=120, user_id=LOCAL_USER_ID)
    preview = await service.preview(
        "planner.complete", {"planner_item_id": item_id, "state": True}
    )
    token = preview["confirm_token"]
    assert preview["risk"] == "low"

    async with sessions() as session:
        stored = await session.get(ActionPreview, 1)
        assert stored is not None
        assert stored.token_hash != token

    result = await service.execute("planner.complete", token)
    assert result["executed"] is True
    assert result["verified"] is True

    with pytest.raises(ActionError):
        await service.execute("planner.complete", token)

    await engine.dispose()


@pytest.mark.asyncio
async def test_remote_mismatch_does_not_change_local_planner_state():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    sessions = make_session_factory(engine)
    async with sessions() as session:
        item = PlannerItem(
            canvas_id="assignment:321",
            title="Submit project",
            completed=False,
            read=False,
            raw={
                "plannable_id": 321,
                "plannable_type": "assignment",
                "planner_override": {"id": 99},
            },
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)
        item_id = item.id

    service = ActionService(
        sessions,
        ttl_seconds=120,
        user_id=LOCAL_USER_ID,
        canvas=RejectingCanvas(),  # type: ignore[arg-type]
    )
    preview = await service.preview(
        "planner.complete", {"planner_item_id": item_id, "state": True}
    )
    with pytest.raises(ActionError, match="did not confirm"):
        await service.execute("planner.complete", preview["confirm_token"])

    async with sessions() as session:
        stored = await session.get(PlannerItem, item_id)
        assert stored is not None
        assert stored.completed is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_planner_note_preview_payload_and_verified_readback():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    canvas = PlannerNoteCanvas()
    service = ActionService(
        make_session_factory(engine), 120, LOCAL_USER_ID, canvas=canvas  # type: ignore[arg-type]
    )
    preview = await service.preview("planner.note.create", {
        "title": " Review chapter ",
        "details": "Pages 1-10",
        "todo_date": "2026-09-25T09:00:00Z",
    })
    assert preview["operation"] == "create"
    assert preview["fields"] == {
        "title": "Review chapter",
        "details": "Pages 1-10",
        "todo_date": "2026-09-25T09:00:00Z",
        "course_id": None,
    }
    result = await service.execute(
        "planner.note.create", preview["confirm_token"]
    )
    assert result["verified"] is True
    assert canvas.calls[0] == (
        "POST", "/api/v1/planner_notes",
        {"json": {
            "title": "Review chapter",
            "details": "Pages 1-10",
            "todo_date": "2026-09-25T09:00:00Z",
        }},
    )
    assert canvas.calls[1][0:2] == ("GET", "/api/v1/planner_notes/44")
    await engine.dispose()


@pytest.mark.asyncio
async def test_planner_note_readback_failure_consumes_token_without_retry():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    canvas = PlannerNoteCanvas(mismatch=True)
    service = ActionService(
        make_session_factory(engine), 120, LOCAL_USER_ID, canvas=canvas  # type: ignore[arg-type]
    )
    preview = await service.preview("planner.note.create", {
        "title": "Review chapter",
        "details": "Pages 1-10",
        "todo_date": "2026-09-25T09:00:00Z",
    })
    with pytest.raises(ActionError, match="readback mismatch"):
        await service.execute("planner.note.create", preview["confirm_token"])
    with pytest.raises(ActionError, match="expired, used, or mismatched"):
        await service.execute("planner.note.create", preview["confirm_token"])
    assert len([call for call in canvas.calls if call[0] == "POST"]) == 1
    await engine.dispose()
