import base64
import sqlite3
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi.testclient import TestClient

from canvas_helper.config import Settings
from canvas_helper.main import create_app


def server_settings(tmp_path, **overrides) -> Settings:
    values = {
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'server.db'}",
        "data_dir": tmp_path / "data",
        "deployment_mode": "server",
        "credential_encryption_key": base64.urlsafe_b64encode(b"k" * 32).decode(),
        "session_cookie_secure": False,
        "public_url": "http://testserver",
    }
    values.update(overrides)
    return Settings(**values)


def issue_magic_token(client: TestClient, app, email: str) -> str:
    response = client.post("/api/auth/request-link", json={"email": email})
    assert response.status_code == 202
    url = app.state.email_backend.last_url
    assert url is not None
    return parse_qs(urlsplit(url).query)["magic_token"][0]


def login(client: TestClient, app, email: str) -> None:
    token = issue_magic_token(client, app, email)
    response = client.post("/api/auth/verify", json={"token": token})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]


def csrf_headers(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies["canvas_helper_csrf"]}


def test_magic_link_session_rotation_csrf_logout_and_one_time_use(tmp_path):
    settings = server_settings(tmp_path, session_rotation_seconds=0)
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/me").status_code == 401
        token = issue_magic_token(client, app, "student@example.edu")
        verified = client.post("/api/auth/verify", json={"token": token})
        assert verified.status_code == 200
        original_session = client.cookies["canvas_helper_session"]

        client.cookies.clear()
        assert client.post("/api/auth/verify", json={"token": token}).status_code == 400
        token = issue_magic_token(client, app, "student@example.edu")
        assert client.post("/api/auth/verify", json={"token": token}).status_code == 200

        assert client.get("/api/todos").status_code == 200
        assert client.cookies["canvas_helper_session"] != original_session
        assert client.post("/api/todos", json={"title": "blocked"}).status_code == 403
        created = client.post(
            "/api/todos",
            json={"title": "allowed"},
            headers=csrf_headers(client),
        )
        assert created.status_code == 201
        assert client.post("/api/auth/logout").status_code == 403
        assert client.post(
            "/api/auth/logout", json={}, headers=csrf_headers(client)
        ).status_code == 204
        assert client.get("/api/todos").status_code == 401

    with sqlite3.connect(tmp_path / "server.db") as connection:
        stored = connection.execute(
            "SELECT token_hash FROM magic_link_tokens LIMIT 1"
        ).fetchone()[0]
    assert stored != token
    assert len(stored) == 64


def test_server_credentials_are_isolated_and_ciphertext_at_rest(tmp_path):
    async def canvas(request: httpx.Request) -> httpx.Response:
        bearer = request.headers["Authorization"].removeprefix("Bearer ")
        owner = "one" if bearer == "tenant-one-secret" else "two"
        return httpx.Response(200, json={"id": owner, "name": owner})

    app = create_app(
        server_settings(tmp_path),
        canvas_transport=httpx.MockTransport(canvas),
    )
    saved_cookies: dict[str, str]
    with TestClient(app) as client:
        login(client, app, "one@example.edu")
        first = client.post(
            "/api/setup/token",
            json={
                "canvas_url": "https://canvas.example.edu",
                "token": "tenant-one-secret",
            },
            headers=csrf_headers(client),
        )
        assert first.status_code == 200
        saved_cookies = dict(client.cookies)

        client.cookies.clear()
        login(client, app, "two@example.edu")
        second = client.post(
            "/api/setup/token",
            json={
                "canvas_url": "https://canvas.example.edu",
                "token": "tenant-two-secret",
            },
            headers=csrf_headers(client),
        )
        assert second.status_code == 200
        assert client.get("/api/me").json()["id"] == "two"

        client.cookies.clear()
        for name, value in saved_cookies.items():
            client.cookies.set(name, value)
        assert client.get("/api/me").json()["id"] == "one"

    database = (tmp_path / "server.db").read_bytes()
    assert b"tenant-one-secret" not in database
    assert b"tenant-two-secret" not in database
    with sqlite3.connect(tmp_path / "server.db") as connection:
        credentials = connection.execute(
            "SELECT user_id, name, ciphertext, nonce, key_version "
            "FROM encrypted_credentials"
        ).fetchall()
    assert len({row[0] for row in credentials}) == 2
    assert {row[1] for row in credentials} == {"canvas_token", "canvas_base_url"}
    assert all(row[2] and len(row[3]) == 12 and row[4] == 1 for row in credentials)
