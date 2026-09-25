import httpx
from fastapi.testclient import TestClient

from canvas_helper.config import Settings
from canvas_helper.main import create_app


def test_health_and_local_write_protection(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/api/me").json() == {"configured": False}
        assert client.post("/api/todos", json={"title": "x"}).status_code == 403
        assert (
            client.post(
                "/api/todos",
                json={"title": "x"},
                headers={"X-Canvas-Helper": "1", "Origin": "https://evil.test"},
            ).status_code
            == 403
        )
        response = client.post(
            "/api/todos",
            json={"title": "Safe todo"},
            headers={"X-Canvas-Helper": "1", "Origin": "http://testserver"},
        )
        assert response.status_code == 201
        assert "token" not in response.text.lower()
        deleted = client.delete(
            f"/api/todos/{response.json()['id']}",
            headers={"X-Canvas-Helper": "1", "Origin": "http://testserver"},
        )
        assert deleted.status_code == 204


def test_setup_uses_and_persists_validated_canvas_url(tmp_path):
    profile_requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal profile_requests
        assert request.url.host == "canvas.example.edu"
        assert request.headers["Authorization"] == "Bearer secret-value"
        profile_requests += 1
        return httpx.Response(200, json={"id": 7, "name": "Test User"})

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
    )
    app = create_app(settings, canvas_transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        response = client.post(
            "/api/setup/token",
            json={
                "canvas_url": "https://canvas.example.edu",
                "token": "secret-value",
            },
            headers={"X-Canvas-Helper": "1", "Origin": "http://testserver"},
        )
        assert response.status_code == 200
        assert response.json()["canvas_user"]["id"] == 7
        assert "secret-value" not in response.text
        me = client.get("/api/me")
        assert me.status_code == 200
        assert me.json()["name"] == "Test User"
        assert profile_requests == 1
        assert app.state.secret_store.get("canvas_base_url") == (
            "https://canvas.example.edu"
        )


def test_server_mode_fails_closed_until_auth_is_implemented(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}",
        data_dir=tmp_path / "data",
        deployment_mode="server",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        response = client.get("/api/todos")
        assert response.status_code == 401
        assert response.json()["detail"] == "Authentication is required"
