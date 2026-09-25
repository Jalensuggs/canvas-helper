"""Server worker entrypoint: ``python -m canvas_helper.worker``."""

import asyncio
import hashlib
import os

from .canvas.client import CanvasClient
from .canvas.pool import CanvasClientPool
from .config import get_settings
from .credentials import CredentialVault
from .db import init_db, make_engine, make_session_factory, migrate_database
from .job_queue import SyncJobQueue
from .retention import retention_loop
from .secret_store import (
    KeyringSecretStore,
    MigratingKeyringSecretStore,
    RestrictedFileSecretStore,
)


def _run_migrations_enabled() -> bool:
    return os.getenv("CANVAS_HELPER_RUN_MIGRATIONS", "true").strip().lower() != "false"


async def run_worker() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url, settings)
    sessions = make_session_factory(engine)
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if settings.database_url.endswith("/:memory:"):
        await init_db(engine)
    elif _run_migrations_enabled():
        # The API container owns migrations in the default compose topology;
        # honour the same switch the shell entrypoint reads.
        await migrate_database(settings.database_url)

    if settings.allow_development_secret_file:
        store = RestrictedFileSecretStore(settings.data_dir / "secrets.json")
    else:
        suffix = hashlib.sha256(
            str(settings.data_dir.expanduser().resolve()).encode()
        ).hexdigest()[:12]
        store = MigratingKeyringSecretStore(
            KeyringSecretStore(f"{settings.keyring_service}:{suffix}"),
            settings.data_dir / "secrets.json",
        )
    credentials = CredentialVault(
        mode=settings.deployment_mode,
        desktop_store=store,
        encryption_key=settings.credential_encryption_key,
        key_version=settings.credential_key_version,
    )
    clients = CanvasClientPool()

    async def canvas_for_user(user_id: str) -> CanvasClient:
        async with sessions() as session:
            token = await credentials.get(session, user_id, "canvas_token")
            if not token:
                raise RuntimeError("Canvas token is not configured")
            base_url = (
                await credentials.get(session, user_id, "canvas_base_url")
                or str(settings.canvas_base_url)
            )
            return await clients.get(user_id, base_url, token)

    queue = SyncJobQueue(
        sessions,
        canvas_for_user,
        materials_root=settings.data_dir / "materials",
        poll_seconds=settings.worker_poll_seconds,
        event_poll_seconds=settings.sync_event_poll_seconds,
        settings=settings,
    )
    try:
        await asyncio.gather(
            queue.worker_loop(),
            queue.scheduler_loop(),
            retention_loop(sessions, settings),
        )
    finally:
        await clients.close()
        await engine.dispose()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
