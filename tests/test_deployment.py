import base64

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from canvas_helper.config import Settings
from canvas_helper.main import create_app


def test_built_frontend_and_spa_fallback_are_served_safely(tmp_path):
    frontend = tmp_path / "dist"
    (frontend / "assets").mkdir(parents=True)
    (frontend / "index.html").write_text("<main>frontend</main>")
    (frontend / "assets" / "app.js").write_text("console.log('ok')")
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        data_dir=tmp_path / "data",
        frontend_dir=frontend,
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/", headers={"Accept": "text/html"}).status_code == 200
        route = client.get("/courses/42", headers={"Accept": "text/html"})
        assert route.text == "<main>frontend</main>"
        asset = client.get("/assets/app.js")
        assert asset.status_code == 200
        assert "immutable" in asset.headers["cache-control"]
        assert client.get("/missing.js").status_code == 404
        assert client.get("/api/not-real", headers={"Accept": "text/html"}).status_code == 404
        assert client.get("/not-a-page", headers={"Accept": "application/json"}).status_code == 404
        assert client.get("/%2e%2e/secret", headers={"Accept": "text/html"}).status_code == 404


def test_production_desktop_app_smoke(tmp_path):
    frontend = tmp_path / "dist"
    frontend.mkdir()
    (frontend / "index.html").write_text("<div id=\"root\"></div>")
    settings = Settings(
        environment="production",
        deployment_mode="local_desktop",
        database_url="sqlite+aiosqlite:///:memory:",
        data_dir=tmp_path / "data",
        frontend_dir=frontend,
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/", headers={"Accept": "text/html"}).status_code == 200


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"credential_encryption_key": None}, "credential_encryption_key"),
        ({"session_cookie_secure": False}, "secure session cookies"),
        ({"public_url": "http://example.test"}, "public_url"),
        ({"email_backend": "development"}, "SMTP email"),
        ({"smtp_password": None}, "SMTP credentials"),
        ({"database_url": "sqlite+aiosqlite:///data/app.db"}, "PostgreSQL"),
        ({"debug": True}, "debug"),
    ],
)
def test_production_server_configuration_fails_closed(override, message):
    values = {
        "environment": "production",
        "deployment_mode": "server",
        "database_url": "postgresql+asyncpg://canvas:secret@db/canvas",
        "credential_encryption_key": base64.urlsafe_b64encode(b"k" * 32).decode(),
        "public_url": "https://canvas.example.test",
        "email_backend": "smtp",
        "smtp_host": "smtp.example.test",
        "smtp_username": "canvas",
        "smtp_password": "secret",
    }
    values.update(override)
    with pytest.raises(ValidationError, match=message):
        Settings(**values)


def test_production_server_configuration_accepts_complete_settings():
    settings = Settings(
        environment="production",
        deployment_mode="server",
        database_url="postgresql+asyncpg://canvas:secret@db/canvas",
        credential_encryption_key=base64.urlsafe_b64encode(b"k" * 32).decode(),
        public_url="https://canvas.example.test",
        email_backend="smtp",
        smtp_host="smtp.example.test",
        smtp_username="canvas",
        smtp_password="secret",
    )
    assert settings.environment == "production"
