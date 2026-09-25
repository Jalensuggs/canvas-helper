import sqlite3

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import select

from canvas_helper.config import Settings
from canvas_helper.db import make_engine, make_session_factory, migrate_database
from canvas_helper.main import create_app
from canvas_helper.models import Course, LOCAL_USER_ID, User
from canvas_helper.tenancy import get_current_user


@pytest.mark.asyncio
async def test_legacy_database_is_migrated_without_losing_local_data(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE courses (
                id INTEGER NOT NULL PRIMARY KEY,
                name VARCHAR(300) NOT NULL,
                course_code VARCHAR(100),
                workflow_state VARCHAR(50),
                raw JSON NOT NULL,
                synced_at DATETIME NOT NULL
            );
            CREATE TABLE planner_items (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                canvas_id VARCHAR(150) NOT NULL UNIQUE,
                course_id INTEGER,
                title VARCHAR(500) NOT NULL,
                item_type VARCHAR(80),
                due_at DATETIME,
                completed BOOLEAN NOT NULL,
                read BOOLEAN NOT NULL,
                raw JSON NOT NULL
            );
            CREATE TABLE local_todos (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                title VARCHAR(500) NOT NULL,
                due_at DATETIME,
                completed BOOLEAN NOT NULL,
                created_at DATETIME NOT NULL
            );
            INSERT INTO courses VALUES
                (42, 'Legacy course', 'LEG42', 'available', '{}', CURRENT_TIMESTAMP);
            INSERT INTO planner_items
                (canvas_id, course_id, title, completed, read, raw)
                VALUES ('assignment:7', 42, 'Legacy planner', 0, 0, '{}');
            INSERT INTO local_todos (title, completed, created_at)
                VALUES ('Keep me', 0, CURRENT_TIMESTAMP);
            """
        )

    url = f"sqlite+aiosqlite:///{path}"
    await migrate_database(url)

    engine = make_engine(url)
    sessions = make_session_factory(engine)
    async with sessions() as session:
        legacy = await session.scalar(
            select(Course).where(
                Course.user_id == LOCAL_USER_ID, Course.canvas_id == 42
            )
        )
        assert legacy is not None
        assert legacy.name == "Legacy course"

        second_user = User(id="tenant-two", display_name="Tenant Two")
        session.add(second_user)
        session.add(
            Course(
                user_id=second_user.id,
                canvas_id=42,
                name="Same Canvas ID, other tenant",
                raw={},
            )
        )
        await session.commit()
        courses = (
            await session.scalars(
                select(Course).where(Course.canvas_id == 42).order_by(Course.user_id)
            )
        ).all()
        assert {row.user_id for row in courses} == {LOCAL_USER_ID, "tenant-two"}
    await engine.dispose()


def test_todo_routes_do_not_cross_tenant_boundary(tmp_path):
    path = tmp_path / "tenant-routes.db"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{path}",
        data_dir=tmp_path / "data",
    )
    app = create_app(settings)

    async def selected_user(request: Request) -> User:
        user_id = request.headers.get("X-Test-User", LOCAL_USER_ID)
        return User(id=user_id, display_name=user_id)

    app.dependency_overrides[get_current_user] = selected_user
    with TestClient(app) as client:
        with sqlite3.connect(path) as connection:
            connection.execute(
                "INSERT INTO users (id, display_name, created_at) "
                "VALUES ('tenant-two', 'Tenant Two', CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "INSERT INTO local_todos "
                "(user_id, title, completed, created_at) "
                "VALUES (?, 'Local only', 0, CURRENT_TIMESTAMP)",
                (LOCAL_USER_ID,),
            )
            other_id = connection.execute(
                "INSERT INTO local_todos "
                "(user_id, title, completed, created_at) "
                "VALUES ('tenant-two', 'Other only', 0, CURRENT_TIMESTAMP) "
                "RETURNING id"
            ).fetchone()[0]
            connection.commit()

        assert [item["title"] for item in client.get("/api/todos").json()] == [
            "Local only"
        ]
        other = client.get("/api/todos", headers={"X-Test-User": "tenant-two"})
        assert [item["title"] for item in other.json()] == ["Other only"]
        forbidden = client.patch(
            f"/api/todos/{other_id}",
            json={"completed": True},
            headers={
                "X-Test-User": LOCAL_USER_ID,
                "X-Canvas-Helper": "1",
                "Origin": "http://testserver",
            },
        )
        assert forbidden.status_code == 404
