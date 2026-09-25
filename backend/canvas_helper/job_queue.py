import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .canvas.client import CanvasClient
from .config import Settings, get_settings
from .models import CanvasAccount, SyncJob, SyncSchedule
from .sync import SyncService

logger = logging.getLogger(__name__)

SYNC_JOBS = frozenset(
    {"all", "courses", "assignments", "deadlines", "planner", "materials", "announcements"}
)
SCHEDULE_DEFAULTS = {
    "announcements": 5 * 60,
    "planner": 5 * 60,
    "deadlines": 5 * 60,
    "materials": 15 * 60,
    "courses": 24 * 60 * 60,
}
TERMINAL_STATES = frozenset({"success", "failed", "cancelled"})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def job_out(row: SyncJob) -> dict[str, Any]:
    def iso(value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    return {
        "id": row.id,
        "job": row.job,
        "status": row.status,
        "attempts": row.attempts,
        "max_attempts": row.max_attempts,
        "created_at": iso(row.created_at),
        "started_at": iso(row.started_at),
        "finished_at": iso(row.finished_at),
        "updated_at": iso(row.updated_at),
        "available_at": iso(row.available_at),
        "cancel_requested": row.cancel_requested,
        "result": row.result,
        "error": row.error,
        "change_count": row.change_count,
        "rate_limit_remaining": row.rate_limit_remaining,
    }


def adaptive_interval(job: str, change_count: int, rate_remaining: float | None) -> int:
    """Return a bounded interval influenced by Canvas pressure and observed churn."""
    base = SCHEDULE_DEFAULTS[job]
    factor = 1.0
    if change_count == 0:
        factor *= 1.5
    elif change_count >= 25:
        factor *= 0.5
    elif change_count >= 5:
        factor *= 0.75
    if rate_remaining is not None:
        if rate_remaining < 100:
            factor *= 4
        elif rate_remaining < 300:
            factor *= 2
    return max(60, min(48 * 60 * 60, int(base * factor)))


def result_change_count(result: dict[str, Any]) -> int:
    total = 0
    for value in result.values():
        if isinstance(value, dict):
            total += int(value.get("changed", value.get("count", 0)) or 0)
    if "changed" in result or "count" in result:
        total += int(result.get("changed", result.get("count", 0)) or 0)
    return total


class SyncJobQueue:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        canvas_for_user: Callable[[str], Awaitable[CanvasClient]],
        *,
        lease_seconds: int = 120,
        poll_seconds: float = 2.0,
        materials_root: Path | None = None,
        event_poll_seconds: float = 3.0,
        settings: Settings | None = None,
    ):
        self.sessions = sessions
        self.canvas_for_user = canvas_for_user
        self.settings = settings or get_settings()
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self.event_poll_seconds = event_poll_seconds
        self.materials_root = materials_root
        self.worker_id = f"worker-{uuid.uuid4()}"
        self._worker_task: asyncio.Task[None] | None = None
        self._scheduler_task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._last_recovery = 0.0

    async def enqueue(
        self, user_id: str, job: str, *, max_attempts: int = 4
    ) -> tuple[SyncJob, bool]:
        if job not in SYNC_JOBS:
            raise ValueError("Unknown sync job")
        canonical = "deadlines" if job == "assignments" else job
        dedupe_key = f"{user_id}:{canonical}"
        async with self.sessions() as session:
            existing = await session.scalar(
                select(SyncJob).where(SyncJob.dedupe_key == dedupe_key)
            )
            if existing is not None:
                return existing, False
            row = SyncJob(
                id=str(uuid.uuid4()),
                user_id=user_id,
                job=canonical,
                status="queued",
                dedupe_key=dedupe_key,
                max_attempts=max_attempts,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(SyncJob).where(SyncJob.dedupe_key == dedupe_key)
                )
                if existing is None:
                    raise
                return existing, False
            await session.refresh(row)
        self._wake.set()
        return row, True

    async def recover_stale(self) -> int:
        now = utcnow()
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(SyncJob).where(
                        SyncJob.status == "running",
                        SyncJob.lease_expires_at < now,
                    )
                )
            ).all()
            for row in rows:
                row.lease_owner = None
                row.lease_expires_at = None
                row.error = "Worker lease expired"
                row.updated_at = now
                if row.cancel_requested:
                    row.status = "cancelled"
                    row.finished_at = now
                    row.dedupe_key = None
                elif row.attempts >= row.max_attempts:
                    row.status = "failed"
                    row.finished_at = now
                    row.dedupe_key = None
                else:
                    row.status = "retrying"
                    row.available_at = now
            await session.commit()
            return len(rows)

    async def claim(self) -> SyncJob | None:
        # Lease recovery scans every running job, so it runs on its own slow
        # cadence rather than on every poll of an otherwise idle queue.
        loop_time = asyncio.get_running_loop().time()
        if loop_time - self._last_recovery >= max(1.0, self.lease_seconds / 2):
            self._last_recovery = loop_time
            await self.recover_stale()
        now = utcnow()
        async with self.sessions() as session:
            candidates = list(
                (
                    await session.scalars(
                        select(SyncJob)
                        .where(
                            SyncJob.status.in_(("queued", "retrying")),
                            SyncJob.available_at <= now,
                        )
                        .order_by(SyncJob.created_at)
                    )
                ).all()
            )
            if not candidates:
                return None
            oldest_by_user: dict[str, SyncJob] = {}
            for row in candidates:
                oldest_by_user.setdefault(row.user_id, row)
            last_claims = dict(
                (
                    await session.execute(
                        select(SyncJob.user_id, func.max(SyncJob.started_at))
                        .where(SyncJob.user_id.in_(oldest_by_user))
                        .group_by(SyncJob.user_id)
                    )
                ).all()
            )
            epoch = datetime(1970, 1, 1)

            def fairness_key(row: SyncJob) -> tuple[datetime, datetime]:
                claimed = last_claims.get(row.user_id)
                if claimed is not None and claimed.tzinfo is not None:
                    claimed = claimed.replace(tzinfo=None)
                created = row.created_at.replace(tzinfo=None) if row.created_at.tzinfo else row.created_at
                return claimed or epoch, created

            candidate = min(oldest_by_user.values(), key=fairness_key)
            lease_until = now + timedelta(seconds=self.lease_seconds)
            claimed = await session.execute(
                update(SyncJob)
                .where(
                    SyncJob.id == candidate.id,
                    SyncJob.status.in_(("queued", "retrying")),
                    SyncJob.available_at <= now,
                )
                .values(
                    status="running",
                    attempts=SyncJob.attempts + 1,
                    started_at=now,
                    updated_at=now,
                    lease_owner=self.worker_id,
                    lease_expires_at=lease_until,
                )
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                await session.rollback()
                return None
            await session.commit()
            return await session.get(SyncJob, candidate.id)

    async def _finish(
        self,
        job_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        canvas: CanvasClient | None = None,
    ) -> None:
        now = utcnow()
        async with self.sessions() as session:
            row = await session.get(SyncJob, job_id)
            if row is None or row.lease_owner != self.worker_id:
                return
            if row.cancel_requested:
                status = "cancelled"
            row.status = status
            row.result = result
            row.error = error
            row.updated_at = now
            row.lease_owner = None
            row.lease_expires_at = None
            if status in TERMINAL_STATES:
                row.finished_at = now
                row.dedupe_key = None
            if result is not None:
                row.change_count = result_change_count(result)
            if canvas and canvas.rate_limit_remaining is not None:
                row.rate_limit_remaining = int(canvas.rate_limit_remaining)
            if status == "success" and row.job in SCHEDULE_DEFAULTS:
                await self._update_schedule(session, row, now)
            await session.commit()
        self._wake.set()

    async def _retry_or_fail(self, row: SyncJob, exc: BaseException) -> None:
        now = utcnow()
        async with self.sessions() as session:
            stored = await session.get(SyncJob, row.id)
            if stored is None or stored.lease_owner != self.worker_id:
                return
            stored.error = f"{type(exc).__name__}: {str(exc)[:1000]}"
            stored.updated_at = now
            stored.lease_owner = None
            stored.lease_expires_at = None
            if stored.cancel_requested:
                stored.status = "cancelled"
                stored.finished_at = now
                stored.dedupe_key = None
            elif stored.attempts >= stored.max_attempts:
                stored.status = "failed"
                stored.finished_at = now
                stored.dedupe_key = None
            else:
                stored.status = "retrying"
                stored.available_at = now + timedelta(
                    seconds=min(300, 2 ** max(0, stored.attempts - 1))
                )
            await session.commit()
        self._wake.set()

    async def execute(self, row: SyncJob) -> None:
        canvas: CanvasClient | None = None
        heartbeat = asyncio.create_task(self._heartbeat(row.id))
        try:
            canvas = await self.canvas_for_user(row.user_id)
            result = await SyncService(
                self.sessions,
                canvas,
                row.user_id,
                self.materials_root,
                settings=self.settings,
            ).execute(row.job)
        except asyncio.CancelledError as exc:
            await self._retry_or_fail(row, exc)
            raise
        except Exception as exc:
            logger.exception("Sync job %s failed", row.id)
            await self._retry_or_fail(row, exc)
        else:
            await self._finish(row.id, status="success", result=result, canvas=canvas)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat(self, job_id: str) -> None:
        while True:
            await asyncio.sleep(max(1, self.lease_seconds / 3))
            now = utcnow()
            async with self.sessions() as session:
                renewed = await session.execute(
                    update(SyncJob)
                    .where(
                        SyncJob.id == job_id,
                        SyncJob.status == "running",
                        SyncJob.lease_owner == self.worker_id,
                    )
                    .values(
                        lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                        updated_at=now,
                    )
                    .execution_options(synchronize_session=False)
                )
                await session.commit()
                if renewed.rowcount != 1:
                    return

    async def run_once(self) -> bool:
        row = await self.claim()
        if row is None:
            return False
        await self.execute(row)
        return True

    async def worker_loop(self) -> None:
        while True:
            try:
                worked = await self.run_once()
            except Exception:
                logger.exception("Sync worker iteration failed")
                await asyncio.sleep(self.poll_seconds)
                continue
            if worked:
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def _update_schedule(
        self, session: AsyncSession, row: SyncJob, now: datetime
    ) -> None:
        schedule = await session.scalar(
            select(SyncSchedule).where(
                SyncSchedule.user_id == row.user_id, SyncSchedule.job == row.job
            )
        )
        changes = row.change_count or 0
        interval = adaptive_interval(row.job, changes, row.rate_limit_remaining)
        if schedule is None:
            schedule = SyncSchedule(user_id=row.user_id, job=row.job, next_run_at=now)
            session.add(schedule)
        schedule.interval_seconds = interval
        schedule.recent_change_count = changes
        schedule.rate_limit_remaining = row.rate_limit_remaining
        schedule.next_run_at = now + timedelta(seconds=interval)
        schedule.updated_at = now

    async def ensure_schedules(self, user_id: str) -> int:
        """Create a user's automatic schedules without triggering an immediate sync."""
        now = utcnow()
        created = 0
        async with self.sessions() as session:
            for job, interval in SCHEDULE_DEFAULTS.items():
                schedule = await session.scalar(
                    select(SyncSchedule).where(
                        SyncSchedule.user_id == user_id,
                        SyncSchedule.job == job,
                    )
                )
                if schedule is None:
                    session.add(
                        SyncSchedule(
                            user_id=user_id,
                            job=job,
                            next_run_at=now + timedelta(seconds=interval),
                            interval_seconds=interval,
                        )
                    )
                    created += 1
            try:
                await session.commit()
            except IntegrityError:
                # A concurrent request or scheduler may have initialized them.
                await session.rollback()
                return 0
        return created

    async def schedule_due(self) -> int:
        now = utcnow()
        due: list[tuple[str, str]] = []
        async with self.sessions() as session:
            users = list(
                (await session.scalars(select(CanvasAccount.user_id).distinct())).all()
            )
            if not users:
                return 0
            # One query for every schedule of every active user, instead of one
            # query per user per job on each scheduler tick.
            existing = {
                (row.user_id, row.job): row
                for row in (
                    await session.scalars(
                        select(SyncSchedule).where(SyncSchedule.user_id.in_(users))
                    )
                ).all()
            }
            for user_id in users:
                for job, interval in SCHEDULE_DEFAULTS.items():
                    schedule = existing.get((user_id, job))
                    if schedule is None:
                        session.add(
                            SyncSchedule(
                                user_id=user_id,
                                job=job,
                                next_run_at=now,
                                interval_seconds=interval,
                            )
                        )
                        due.append((user_id, job))
                    elif aware(schedule.next_run_at) <= now:
                        schedule.next_run_at = now + timedelta(
                            seconds=schedule.interval_seconds
                        )
                        schedule.updated_at = now
                        due.append((user_id, job))
            try:
                await session.commit()
            except IntegrityError:
                # Another scheduler may have initialized the same user/job.
                await session.rollback()
                return 0
        for user_id, job in due:
            await self.enqueue(user_id, job)
        return len(due)

    async def scheduler_loop(self) -> None:
        while True:
            try:
                await self.schedule_due()
            except Exception:
                logger.exception("Adaptive sync scheduler iteration failed")
            await asyncio.sleep(30)

    def start(self, *, scheduler: bool = True) -> None:
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self.worker_loop())
        if scheduler and self._scheduler_task is None:
            self._scheduler_task = asyncio.create_task(self.scheduler_loop())

    async def stop(self) -> None:
        tasks = [task for task in (self._scheduler_task, self._worker_task) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._worker_task = self._scheduler_task = None

    async def cancel(self, user_id: str, job_id: str) -> SyncJob | None:
        now = utcnow()
        async with self.sessions() as session:
            row = await session.scalar(
                select(SyncJob).where(SyncJob.id == job_id, SyncJob.user_id == user_id)
            )
            if row is None:
                return None
            if row.status in {"queued", "retrying"}:
                row.status = "cancelled"
                row.finished_at = now
                row.dedupe_key = None
            elif row.status == "running":
                row.cancel_requested = True
            row.updated_at = now
            await session.commit()
            await session.refresh(row)
            return row

    async def wait(self, user_id: str, job_id: str, timeout: float = 30) -> SyncJob:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        # Back off from a snappy first check to a modest steady-state poll, so a
        # long wait costs a handful of queries rather than one every 20ms.
        delay = 0.05
        while True:
            async with self.sessions() as session:
                row = await session.scalar(
                    select(SyncJob).where(
                        SyncJob.id == job_id, SyncJob.user_id == user_id
                    )
                )
                if row is None:
                    raise LookupError(job_id)
                if row.status in TERMINAL_STATES:
                    return row
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(job_id)
            await asyncio.sleep(min(delay, remaining))
            delay = min(delay * 1.6, 1.0)

    async def status(self, user_id: str, limit: int = 30) -> dict[str, Any]:
        async with self.sessions() as session:
            rows = list(
                (
                    await session.scalars(
                        select(SyncJob)
                        .where(SyncJob.user_id == user_id)
                        .order_by(SyncJob.created_at.desc())
                        .limit(limit)
                    )
                ).all()
            )
            running_rows = list(
                (
                    await session.scalars(
                        select(SyncJob)
                        .where(
                            SyncJob.user_id == user_id, SyncJob.status == "running"
                        )
                        .order_by(SyncJob.started_at)
                    )
                ).all()
            )
            last_success = await session.scalar(
                select(SyncJob)
                .where(
                    SyncJob.user_id == user_id, SyncJob.status == "success"
                )
                .order_by(SyncJob.finished_at.desc())
                .limit(1)
            )
            schedules = list(
                (
                    await session.scalars(
                        select(SyncSchedule)
                        .where(SyncSchedule.user_id == user_id)
                        .order_by(SyncSchedule.job)
                    )
                ).all()
            )
        return {
            "last_success": job_out(last_success) if last_success else None,
            "last_sync_at": job_out(last_success)["finished_at"] if last_success else None,
            "running": [job_out(row) for row in running_rows],
            "jobs": [job_out(row) for row in rows],
            "freshness": {
                row.job: {
                    "next_run_at": (
                        row.next_run_at.replace(tzinfo=timezone.utc)
                        if row.next_run_at.tzinfo is None
                        else row.next_run_at
                    ).isoformat(),
                    "interval_seconds": row.interval_seconds,
                    "recent_change_count": row.recent_change_count,
                    "rate_limit_remaining": row.rate_limit_remaining,
                }
                for row in schedules
            },
        }

    async def fingerprint(self, user_id: str) -> tuple[int, str]:
        """Cheap probe telling whether this user's queue state can have moved.

        The full status payload costs four queries, which is far too much to run
        on a timer for every connected browser. This is one aggregate query, and
        the expensive payload is only built when it changes.
        """
        async with self.sessions() as session:
            row = (
                await session.execute(
                    select(
                        func.count(SyncJob.id),
                        func.max(SyncJob.updated_at),
                    ).where(SyncJob.user_id == user_id)
                )
            ).one()
        return int(row[0] or 0), str(row[1] or "")

    async def event_stream(self, user_id: str):
        previous_payload = ""
        previous_mark: tuple[int, str] | None = None
        while True:
            emitted = False
            mark = await self.fingerprint(user_id)
            if mark != previous_mark:
                previous_mark = mark
                encoded = json.dumps(
                    await self.status(user_id), separators=(",", ":"), default=str
                )
                if encoded != previous_payload:
                    previous_payload = encoded
                    emitted = True
                    yield f"event: sync\ndata: {encoded}\n\n"
            if not emitted:
                yield ": keep-alive\n\n"
            await asyncio.sleep(self.event_poll_seconds)
