"""Server worker entrypoint: ``python -m canvas_helper.worker``."""

import asyncio
import hashlib

from .canvas.client import CanvasClient
from .config import get_settings
from .credentials import CredentialVault
from .db import init_db, make_engine, make_session_factory, migrate_database
from .job_queue import SyncJobQueue
from .secret_store import (
    KeyringSecretStore,
    MigratingKeyringSecretStore,
    RestrictedFileSecretStore,
)


async def run_worker() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sessions = make_session_factory(engine)
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if settings.database_url.endswith("/:memory:"):
        await init_db(engine)
    else:
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
    clients: dict[str, CanvasClient] = {}

    async def canvas_for_user(user_id: str) -> CanvasClient:
        async with sessions() as session:
            token = await credentials.get(session, user_id, "canvas_token")
            if not token:
                raise RuntimeError("Canvas token is not configured")
            if user_id not in clients:
                base_url = (
                    await credentials.get(session, user_id, "canvas_base_url")
                    or str(settings.canvas_base_url)
                )
                clients[user_id] = CanvasClient(base_url, token)
            return clients[user_id]

    queue = SyncJobQueue(
        sessions, canvas_for_user, materials_root=settings.data_dir / "materials"
    )
    try:
        await asyncio.gather(queue.worker_loop(), queue.scheduler_loop())
    finally:
        for client in clients.values():
            await client.close()
        await engine.dispose()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
