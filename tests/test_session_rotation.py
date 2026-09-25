"""Rotating a session must not log out requests that were already in flight."""

import base64
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from canvas_helper.auth import token_hash
from canvas_helper.config import Settings
from canvas_helper.main import create_app
from canvas_helper.models import MagicLinkToken, UserSession


def server_settings(tmp_path, **overrides) -> Settings:
    values = dict(
        deployment_mode="server",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}",
        data_dir=tmp_path / "data",
        credential_encryption_key=base64.urlsafe_b64encode(b"k" * 32).decode(),
        public_url="https://canvas.example.test",
        session_cookie_secure=False,
        allow_development_secret_file=True,
    )
    values.update(overrides)
    return Settings(**values)


def sign_in(client: TestClient, app, email: str = "student@example.test") -> None:
    client.post("/api/auth/request-link", json={"email": email})
    backend = app.state.email_backend
    token = backend.last_url.split("magic_token=", 1)[1]
    response = client.post("/api/auth/verify", json={"token": token})
    assert response.status_code == 200


def test_rotation_keeps_the_previous_cookie_working_briefly(tmp_path):
    # Rotate on every request so the race is deterministic.
    settings = server_settings(tmp_path, session_rotation_seconds=0)
    app = create_app(settings)
    with TestClient(app) as client:
        sign_in(client, app)
        stale = client.cookies.get(settings.session_cookie_name)
        stale_csrf = client.cookies.get(settings.csrf_cookie_name)

        # This call rotates the session and hands back new cookies.
        assert client.get("/api/auth/me").json()["authenticated"] is True
        rotated = client.cookies.get(settings.session_cookie_name)
        assert rotated != stale

        # A request that was already in flight still carries the old cookie.
        replay = client.get(
            "/api/auth/me",
            cookies={
                settings.session_cookie_name: stale,
                settings.csrf_cookie_name: stale_csrf,
            },
        )
        assert replay.status_code == 200
        assert replay.json()["authenticated"] is True


def test_expired_grace_token_is_rejected(tmp_path):
    settings = server_settings(tmp_path, session_rotation_seconds=0)
    app = create_app(settings)
    with TestClient(app) as client:
        sign_in(client, app)
        stale = client.cookies.get(settings.session_cookie_name)
        stale_csrf = client.cookies.get(settings.csrf_cookie_name)
        client.get("/api/auth/me")

        import asyncio

        async def expire_grace() -> None:
            async with app.state.session_factory() as session:
                login = await session.scalar(
                    select(UserSession).where(
                        UserSession.previous_token_hash == token_hash(stale)
                    )
                )
                assert login is not None
                login.previous_expires_at = datetime.now(timezone.utc) - timedelta(
                    minutes=5
                )
                await session.commit()

        asyncio.run(expire_grace())

        replay = client.get(
            "/api/auth/me",
            cookies={
                settings.session_cookie_name: stale,
                settings.csrf_cookie_name: stale_csrf,
            },
        )
        assert replay.json()["authenticated"] is False


def test_consumed_magic_link_cannot_be_replayed(tmp_path):
    settings = server_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        client.post("/api/auth/request-link", json={"email": "student@example.test"})
        token = app.state.email_backend.last_url.split("magic_token=", 1)[1]
        assert client.post("/api/auth/verify", json={"token": token}).status_code == 200
        replay = client.post("/api/auth/verify", json={"token": token})
        assert replay.status_code == 400

        import asyncio

        async def consumed_count() -> int:
            async with app.state.session_factory() as session:
                rows = list((await session.scalars(select(MagicLinkToken))).all())
                return sum(1 for row in rows if row.consumed_at is not None)

        assert asyncio.run(consumed_count()) == 1
