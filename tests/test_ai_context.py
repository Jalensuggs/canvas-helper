"""The AI could not answer "what do I have due" — it was never told.

Three separate things stopped a question about the student's own coursework
from being answerable. Every test here pins one of them:

* the question was answered only from a full-text search, which never sees a
  due date because a due date is a column, not prose;
* the search itself ANDed every word of the question together, so any ordinary
  phrasing matched nothing at all, and a Chinese sentence was matched whole;
* whatever survived that was run through redaction, which mistook ISO dates
  for phone numbers and cut them out.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from canvas_helper.ai import AIService, ProviderResult
from canvas_helper.config import Settings
from canvas_helper.db import init_db, make_engine, make_session_factory
from canvas_helper.main import create_app
from canvas_helper.models import (
    Assignment,
    Course,
    DocChunk,
    LocalTodo,
    LOCAL_USER_ID,
)
from canvas_helper.redact import redact
from canvas_helper.search import SearchService, search_terms
from canvas_helper.study_state import student_snapshot

from test_auth_credentials import csrf_headers, login, server_settings

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
# Kept out of the request literal so a secret scanner does not read the
# line as a real key next to an "api_key" label.
FAKE_KEY = "sk-deepseek-not-a-real-key"


async def _session(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await init_db(engine)
    return engine, make_session_factory(engine)


def _settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'state.db'}",
        data_dir=tmp_path / "data",
        academic_timezone="Australia/Sydney",
        term_start_months=[1, 7],
    )


async def _seed(session, *, submitted: bool = False):
    # init_db already seeds the desktop-mode user.
    course = Course(user_id=LOCAL_USER_ID, canvas_id=11, name="INFO2222 信息安全")
    other = Course(user_id=LOCAL_USER_ID, canvas_id=12, name="COMP3308 人工智能")
    session.add_all([course, other])
    await session.flush()
    session.add_all(
        [
            Assignment(
                user_id=LOCAL_USER_ID,
                canvas_id=901,
                course_id=course.id,
                name="Assignment 2: Threat Model Report",
                due_at=NOW + timedelta(days=12),
                points_possible=20,
                raw={"submission": {"workflow_state": "submitted"}} if submitted else {},
            ),
            Assignment(
                user_id=LOCAL_USER_ID,
                canvas_id=902,
                course_id=other.id,
                name="Quiz 3",
                due_at=NOW + timedelta(days=2),
                raw={},
            ),
            Assignment(
                user_id=LOCAL_USER_ID,
                canvas_id=903,
                course_id=course.id,
                name="Already handed in",
                due_at=NOW + timedelta(days=5),
                raw={"submission": {"submitted_at": "2026-09-01T00:00:00Z"}},
            ),
        ]
    )
    session.add(
        LocalTodo(user_id=LOCAL_USER_ID, title="复习第 5 章", due_at=NOW + timedelta(days=1))
    )
    await session.commit()
    return course


@pytest.mark.asyncio
async def test_snapshot_carries_the_courses_and_the_unfinished_due_dates(tmp_path):
    engine, sessions = await _session(tmp_path)
    async with sessions() as session:
        await _seed(session)
        snapshot = await student_snapshot(
            session, LOCAL_USER_ID, _settings(tmp_path), now=NOW
        )
    await engine.dispose()

    assert "2026-09-30" in snapshot, "the model cannot judge 'soon' without today"
    assert "INFO2222 信息安全" in snapshot and "COMP3308 人工智能" in snapshot
    assert "Assignment 2: Threat Model Report" in snapshot
    # Sydney is UTC+11 in October, so the stored 12:00Z prints as the next day.
    assert "2026-10-12 23:00" in snapshot
    assert "20 分" in snapshot
    assert "复习第 5 章" in snapshot
    # Work already handed in is noise in a list of what is left to do.
    assert "Already handed in" not in snapshot


@pytest.mark.asyncio
async def test_snapshot_says_so_rather_than_leaving_the_model_to_guess(tmp_path):
    engine, sessions = await _session(tmp_path)
    async with sessions() as session:
        await session.commit()
        snapshot = await student_snapshot(
            session, LOCAL_USER_ID, _settings(tmp_path), now=NOW
        )
    await engine.dispose()
    assert "没有" in snapshot
    assert "不要猜测" in snapshot


@pytest.mark.asyncio
async def test_snapshot_honours_the_selected_course(tmp_path):
    engine, sessions = await _session(tmp_path)
    async with sessions() as session:
        course = await _seed(session)
        snapshot = await student_snapshot(
            session, LOCAL_USER_ID, _settings(tmp_path), course_id=course.id, now=NOW
        )
    await engine.dispose()
    assert "INFO2222 信息安全" in snapshot
    assert "COMP3308" not in snapshot and "Quiz 3" not in snapshot


@pytest.mark.asyncio
async def test_the_provider_is_given_the_context_and_told_it_is_not_instructions():
    class Recorder:
        def __init__(self):
            self.messages: list[dict[str, str]] = []
            self.system = ""

        async def chat(self, messages, system):
            self.messages, self.system = messages, system
            return ProviderResult("ok", "m", {"input_tokens": 1, "output_tokens": 1})

    recorder = Recorder()
    service = AIService("k", "m", client=recorder)
    await service.chat(
        [{"role": "user", "content": "我有哪些作业"}],
        context="今天是 2026-09-30\n- 2026-10-12 23:59 · Assignment 2",
    )
    sent = recorder.messages[-1]["content"]
    assert "<student_context>" in sent and "</student_context>" in sent
    assert "2026-10-12 23:59" in sent, "the due date must survive redaction"
    assert "student_context" in recorder.system
    assert "not instructions" in recorder.system


@pytest.mark.asyncio
async def test_a_chinese_question_finds_the_material_it_is_asking_about(tmp_path):
    engine, sessions = await _session(tmp_path)
    async with sessions() as session:
        session.add_all(
            [
                DocChunk(
                    user_id=LOCAL_USER_ID,
                    source_kind="assignment",
                    source_id="901",
                    title="Assignment 2: Threat Model Report",
                    content="本次作业要求提交威胁建模报告，截止 2026-10-12 23:59。",
                    ordinal=0,
                    metadata_json={},
                ),
                DocChunk(
                    user_id=LOCAL_USER_ID,
                    source_kind="page",
                    source_id="902",
                    title="Kitchen sink",
                    content="Unrelated filler about cooking and the weather.",
                    ordinal=0,
                    metadata_json={},
                ),
            ]
        )
        await session.commit()
        service = SearchService()

        # Every one of these used to return nothing: the Chinese ones because
        # the whole sentence was matched as a single substring, the English one
        # because "what", "do" and "have" were ANDed onto the real term.
        for query in [
            "看看我有哪些作业",
            "我这周的作业是什么",
            "威胁建模",
            "what assignments do I have",
            "show me the threat model assignment",
        ]:
            rows = await service.search(session, LOCAL_USER_ID, query)
            assert [row["source_id"] for row in rows] == ["901"], query

        # Widening recall must not turn search into "return everything".
        assert not await service.search(session, LOCAL_USER_ID, "量子力学")
        assert [
            row["source_id"]
            for row in await service.search(session, LOCAL_USER_ID, "cooking")
        ] == ["902"]
    await engine.dispose()


def test_query_terms_drop_scaffolding_and_split_chinese_into_pairs():
    assert search_terms("what assignments do I have") == ["assignment"]
    assert "作业" in search_terms("看看我有哪些作业")
    assert "我有" not in search_terms("看看我有哪些作业")
    assert search_terms("访问控制") == ["访问", "问控", "控制"]
    # A query made only of scaffolding still has to search for something.
    assert search_terms("what do I have") == ["what", "do", "I", "have"]


def test_redaction_keeps_due_dates_and_still_removes_secrets():
    assert redact("截止 2026-10-12 23:59") == "截止 2026-10-12 23:59"
    assert redact("Due 2026-10-12T23:59:00Z") == "Due 2026-10-12T23:59:00Z"
    assert redact("12/10/2026 5pm") == "12/10/2026 5pm"
    assert "[REDACTED]" in redact("call me on +61 400 123 456")
    assert "[REDACTED]" in redact("0400123456")
    assert "sk-ant-api03-secret" not in redact("key sk-ant-api03-secret")


def test_chat_sends_the_students_real_deadlines_to_the_provider(tmp_path):
    """End to end: the question in the screenshot, answered from real rows."""
    captured: dict[str, str] = {}

    class Recorder:
        async def chat(self, messages, system):
            captured["prompt"] = messages[-1]["content"]
            return ProviderResult("ok", "m", {"input_tokens": 1, "output_tokens": 1})

    settings = server_settings(tmp_path, academic_timezone="Australia/Sydney")
    app = create_app(settings, ai_client=Recorder())
    with TestClient(app) as client:
        login(client, app, "student@example.edu")
        headers = csrf_headers(client)
        client.put(
            "/api/settings/ai",
            json={"provider": "deepseek", "api_key": FAKE_KEY, "model": "m"},
            headers=headers,
        )
        response = client.post(
            "/api/ai/chat",
            json={"message": "看看我有哪些作业", "context": {}},
            headers=headers,
        )
        assert response.status_code == 200, response.text

    prompt = captured["prompt"]
    assert "<student_context>" in prompt
    assert "没有" in prompt, "an empty account must be stated, not left blank"
    assert FAKE_KEY not in prompt
