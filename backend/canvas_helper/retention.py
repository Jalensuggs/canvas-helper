"""Periodic cleanup of append-only bookkeeping tables.

Sync jobs, magic-link tokens, expired sessions and note revisions are all
written far more often than they are read. Without a sweep they are the tables
that quietly grow without bound on a long-lived deployment.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import Settings
from .models import MagicLinkToken, Note, NoteRevision, SyncJob, UserSession

logger = logging.getLogger(__name__)

TERMINAL_STATES = ("success", "failed", "cancelled")


async def sweep_once(session: AsyncSession, settings: Settings) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    removed: dict[str, int] = {}

    if settings.sync_job_retention_days > 0:
        cutoff = now - timedelta(days=settings.sync_job_retention_days)
        result = await session.execute(
            delete(SyncJob).where(
                SyncJob.status.in_(TERMINAL_STATES),
                SyncJob.finished_at.is_not(None),
                SyncJob.finished_at < cutoff,
            )
        )
        removed["sync_jobs"] = result.rowcount or 0

    if settings.magic_link_retention_days > 0:
        cutoff = now - timedelta(days=settings.magic_link_retention_days)
        result = await session.execute(
            delete(MagicLinkToken).where(MagicLinkToken.expires_at < cutoff)
        )
        removed["magic_link_tokens"] = result.rowcount or 0

    if settings.user_session_retention_days > 0:
        cutoff = now - timedelta(days=settings.user_session_retention_days)
        result = await session.execute(
            delete(UserSession).where(UserSession.expires_at < cutoff)
        )
        removed["user_sessions"] = result.rowcount or 0

    keep = settings.note_revision_keep_per_note
    if keep > 0:
        # Autosave writes a revision per edit, so trim each note's tail.
        note_ids = (
            await session.scalars(select(Note.id).where(Note.deleted_at.is_(None)))
        ).all()
        trimmed = 0
        for note_id in note_ids:
            versions = (
                await session.scalars(
                    select(NoteRevision.version)
                    .where(NoteRevision.note_id == note_id)
                    .order_by(NoteRevision.version.desc())
                    .offset(keep)
                )
            ).all()
            if versions:
                result = await session.execute(
                    delete(NoteRevision).where(
                        NoteRevision.note_id == note_id,
                        NoteRevision.version.in_(versions),
                    )
                )
                trimmed += result.rowcount or 0
        removed["note_revisions"] = trimmed

    await session.commit()
    return removed


async def retention_loop(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    interval = settings.retention_sweep_seconds
    if interval <= 0:
        return
    while True:
        try:
            async with sessions() as session:
                removed = await sweep_once(session, settings)
            if any(removed.values()):
                logger.info("Retention sweep removed %s", removed)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Retention sweep failed")
        await asyncio.sleep(interval)
