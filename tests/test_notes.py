import sqlite3

from fastapi import Request
from fastapi.testclient import TestClient

from canvas_helper.config import Settings
from canvas_helper.main import create_app
from canvas_helper.models import LOCAL_USER_ID, User
from canvas_helper.tenancy import get_current_user


WRITE = {"X-Canvas-Helper": "1", "Origin": "http://testserver"}


def test_note_crud_history_restore_export_and_concurrency(tmp_path):
    app = create_app(Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'notes.db'}",
        data_dir=tmp_path / "data",
    ))
    with TestClient(app) as client:
        created = client.post(
            "/api/notes",
            json={"title": "Research", "markdown": "# One"},
            headers=WRITE,
        )
        assert created.status_code == 201
        note = created.json()
        assert note["version"] == 1

        saved = client.patch(
            f"/api/notes/{note['id']}",
            json={"version": 1, "markdown": "# Two"},
            headers=WRITE,
        )
        assert saved.status_code == 200
        assert saved.json()["version"] == 2
        assert client.patch(
            f"/api/notes/{note['id']}",
            json={"version": 1, "markdown": "stale"},
            headers=WRITE,
        ).status_code == 409

        history = client.get(f"/api/notes/{note['id']}/revisions").json()
        assert [row["version"] for row in history] == [2, 1]
        exported = client.get(f"/api/notes/{note['id']}/export")
        assert exported.headers["content-type"].startswith("text/markdown")
        assert exported.text == "# Research\n\n# Two"

        deleted = client.request(
            "DELETE", f"/api/notes/{note['id']}",
            json={"version": 2}, headers=WRITE,
        )
        assert deleted.json()["deleted_at"]
        assert client.get("/api/notes").json() == []
        restored = client.post(
            f"/api/notes/{note['id']}/restore",
            json={"version": 3}, headers=WRITE,
        )
        assert restored.json()["deleted_at"] is None


def test_note_tenant_idor_and_target_ownership(tmp_path):
    path = tmp_path / "tenant-notes.db"
    app = create_app(Settings(
        database_url=f"sqlite+aiosqlite:///{path}",
        data_dir=tmp_path / "data",
    ))

    async def selected_user(request: Request) -> User:
        user_id = request.headers.get("X-Test-User", LOCAL_USER_ID)
        return User(id=user_id, display_name=user_id)

    app.dependency_overrides[get_current_user] = selected_user
    with TestClient(app) as client:
        with sqlite3.connect(path) as connection:
            connection.execute(
                "INSERT INTO users (id, display_name, created_at) "
                "VALUES ('tenant-two', 'Two', CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "INSERT INTO courses "
                "(user_id, canvas_id, name, raw, synced_at) "
                "VALUES ('tenant-two', 777, 'Private', '{}', CURRENT_TIMESTAMP)"
            )
            connection.commit()
        rejected = client.post(
            "/api/notes",
            json={"title": "Bad target", "course_id": 777},
            headers=WRITE,
        )
        assert rejected.status_code == 422
        other = client.post(
            "/api/notes", json={"title": "Private"},
            headers={**WRITE, "X-Test-User": "tenant-two"},
        ).json()
        assert client.get(f"/api/notes/{other['id']}").status_code == 404
        assert client.patch(
            f"/api/notes/{other['id']}",
            json={"version": 1, "markdown": "stolen"},
            headers=WRITE,
        ).status_code == 404
