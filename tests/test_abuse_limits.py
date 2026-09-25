import base64
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from canvas_helper.config import Settings
from canvas_helper.db import make_engine, make_session_factory, sync_database_url
from canvas_helper.main import create_app
from canvas_helper.models import MagicLinkToken, Note, NoteRevision, SyncJob, User
from canvas_helper.retention import sweep_once


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


def test_magic_link_requests_are_capped_per_email(tmp_path):
    settings = server_settings(tmp_path, magic_link_per_email_per_hour=2)
    with TestClient(create_app(settings)) as client:
        payload = {"email": "student@example.test"}
        assert client.post("/api/auth/request-link", json=payload).status_code == 202
        assert client.post("/api/auth/request-link", json=payload).status_code == 202
        blocked = client.post("/api/auth/request-link", json=payload)
        assert blocked.status_code == 429
        assert blocked.headers["retry-after"] == "3600"


def test_magic_link_requests_are_capped_per_address(tmp_path):
    settings = server_settings(
        tmp_path, magic_link_per_email_per_hour=0, magic_link_per_ip_per_hour=2
    )
    with TestClient(create_app(settings)) as client:
        # Distinct addresses, same caller: the per-IP quota still applies.
        assert client.post(
            "/api/auth/request-link", json={"email": "a@example.test"}
        ).status_code == 202
        assert client.post(
            "/api/auth/request-link", json={"email": "b@example.test"}
        ).status_code == 202
        assert client.post(
            "/api/auth/request-link", json={"email": "c@example.test"}
        ).status_code == 429


def test_email_domain_allowlist_rejects_outsiders(tmp_path):
    settings = server_settings(tmp_path, allowed_email_domains=("student.uts.edu.au",))
    with TestClient(create_app(settings)) as client:
        allowed = client.post(
            "/api/auth/request-link", json={"email": "me@student.uts.edu.au"}
        )
        assert allowed.status_code == 202
        rejected = client.post(
            "/api/auth/request-link", json={"email": "someone@gmail.com"}
        )
        assert rejected.status_code == 403


def test_requester_address_is_not_stored_in_plain_text(tmp_path):
    settings = server_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post("/api/auth/request-link", json={"email": "student@example.test"})

    engine = make_engine(sync_database_url(settings.database_url).replace(
        "sqlite:", "sqlite+aiosqlite:"
    ))
    sessions = make_session_factory(engine)

    async def read() -> list[MagicLinkToken]:
        async with sessions() as session:
            return list((await session.scalars(select(MagicLinkToken))).all())

    import asyncio

    rows = asyncio.run(read())
    asyncio.run(engine.dispose())
    assert len(rows) == 1
    assert rows[0].requester_hash
    assert "testclient" not in rows[0].requester_hash
    assert len(rows[0].requester_hash) == 64


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql+asyncpg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("sqlite+aiosqlite:///./x.db", "sqlite:///./x.db"),
        ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
    ],
)
def test_sync_database_url_names_an_installed_driver(url, expected):
    # A bare postgresql:// URL resolves to psycopg2 on SQLAlchemy 2.0, which is
    # not a dependency, so the driver must always be explicit.
    assert sync_database_url(url) == expected


async def test_retention_sweep_trims_finished_jobs_and_old_revisions(tmp_path):
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        data_dir=tmp_path / "data",
        allow_development_secret_file=True,
        sync_job_retention_days=7,
        note_revision_keep_per_note=2,
    )
    engine = make_engine(settings.database_url)
    sessions = make_session_factory(engine)
    from canvas_helper.db import init_db

    await init_db(engine)
    now = datetime.now(timezone.utc)
    async with sessions() as session:
        user = await session.get(User, "00000000-0000-0000-0000-000000000001")
        assert user is not None
        session.add(
            SyncJob(
                id="old",
                user_id=user.id,
                job="courses",
                status="success",
                finished_at=now - timedelta(days=30),
            )
        )
        session.add(
            SyncJob(
                id="recent",
                user_id=user.id,
                job="courses",
                status="success",
                finished_at=now,
            )
        )
        note = Note(user_id=user.id, title="n", markdown="body", version=5)
        session.add(note)
        await session.flush()
        for version in range(1, 6):
            session.add(
                NoteRevision(
                    user_id=user.id,
                    note_id=note.id,
                    version=version,
                    title="n",
                    markdown="body",
                    reason="autosave",
                )
            )
        await session.commit()

        removed = await sweep_once(session, settings)
        assert removed["sync_jobs"] == 1
        assert removed["note_revisions"] == 3
        remaining = list((await session.scalars(select(SyncJob.id))).all())
        assert remaining == ["recent"]
        kept = sorted((await session.scalars(select(NoteRevision.version))).all())
        assert kept == [4, 5]
    await engine.dispose()
