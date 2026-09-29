import base64

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from canvas_helper import db
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


def test_migrations_are_found_when_installed_outside_a_checkout(tmp_path, monkeypatch):
    """The server image installs the package into site-packages.

    There the repository root two levels up is a shared library directory that
    holds no migrations, so resolving them from it crashed every API container
    on startup. The scripts ship as package data; check they are found there
    with no checkout anywhere above.
    """
    site_packages = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
    package = site_packages / "canvas_helper"
    packaged_migrations = package / "migrations"
    packaged_migrations.mkdir(parents=True)
    (packaged_migrations / "env.py").write_text("")
    monkeypatch.setattr(db, "__file__", str(package / "db.py"))
    monkeypatch.chdir(tmp_path)

    assert db._resolve_migrations_dir() == packaged_migrations


def test_migrations_are_found_from_a_source_checkout():
    """The repository layout keeps them at the root, two levels up."""
    resolved = db._resolve_migrations_dir()

    assert (resolved / "env.py").is_file()
    assert (resolved / "versions").is_dir()


def test_frontend_is_found_when_installed_outside_a_checkout(tmp_path, monkeypatch):
    """The server image copies the built frontend next to the working directory.

    The package itself is installed into site-packages, where the path two
    levels up holds no frontend, so resolving from it served a 404 for every
    page while the API itself looked healthy.
    """
    built = tmp_path / "frontend" / "dist"
    built.mkdir(parents=True)
    (built / "index.html").write_text("<div id=\"root\"></div>")
    monkeypatch.chdir(tmp_path)
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:", data_dir=tmp_path / "data")

    with TestClient(create_app(settings)) as client:
        assert client.get("/", headers={"Accept": "text/html"}).status_code == 200


def test_explicit_frontend_dir_still_wins(tmp_path, monkeypatch):
    """An operator pointing the setting somewhere is never second-guessed."""
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    (chosen / "index.html").write_text("<main>chosen</main>")
    decoy = tmp_path / "frontend" / "dist"
    decoy.mkdir(parents=True)
    (decoy / "index.html").write_text("<main>decoy</main>")
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        data_dir=tmp_path / "data",
        frontend_dir=chosen,
    )

    with TestClient(create_app(settings)) as client:
        assert client.get("/", headers={"Accept": "text/html"}).text == "<main>chosen</main>"
