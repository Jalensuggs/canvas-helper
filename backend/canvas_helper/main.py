import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
from pathlib import Path
import sys
from typing import Any

import httpx
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
)
from pydantic import AnyHttpUrl, BaseModel, Field, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse

from .actions import ActionError, ActionService
from .ai import AIService, AIUnavailable, sse_event
from .auth import AuthService
from .canvas.client import CanvasClient
from .canvas.pool import CanvasClientPool
from .config import Settings, get_settings
from .credentials import CredentialVault
from .db import (
    init_db,
    make_engine,
    make_session_factory,
    migrate_database,
    session_dependency,
)
from .models import (
    Announcement,
    Assignment,
    CanvasAccount,
    Course,
    DocChunk,
    Document,
    LOCAL_USER_ID,
    LocalTodo,
    Note,
    NoteRevision,
    PlannerItem,
    SyncJob,
    User,
)
from .email import make_email_backend
from .job_queue import SyncJobQueue, job_out
from .materials import MaterialStore, refresh_document_extraction
from .materials.refresh import backfill_material_extractions_later
from .redact import configure_logging
from .retention import retention_loop
from .secret_store import (
    KeyringSecretStore,
    MigratingKeyringSecretStore,
    RestrictedFileSecretStore,
)
from .security import LocalAccessMiddleware
from .search import (
    RetrievalService,
    SearchFilters,
    SearchService,
    backfill_search_chunks,
)
from .tenancy import CurrentUser


class TokenInput(BaseModel):
    canvas_url: AnyHttpUrl | None = None
    token: str = Field(min_length=8, max_length=4096)


class MagicLinkRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")


class MagicLinkVerify(BaseModel):
    token: str = Field(min_length=20, max_length=500)


class AISettingsInput(BaseModel):
    provider: str = Field(default="anthropic", pattern="^(anthropic|openai)$")
    api_key: str = Field(min_length=8, max_length=4096)
    model: str = Field(min_length=1, max_length=200)


class TodoInput(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    due_at: datetime | None = None


class TodoPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    due_at: datetime | None = None
    completed: bool | None = None


class NoteInput(BaseModel):
    title: str = Field(default="未命名笔记", min_length=1, max_length=500)
    markdown: str = Field(default="", max_length=1_000_000)
    course_id: int | None = None
    assignment_id: int | None = None
    document_id: int | None = None
    chunk_id: int | None = None
    page: int | None = Field(default=None, ge=1)
    slide: int | None = Field(default=None, ge=1)


class NotePatch(BaseModel):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    markdown: str | None = Field(default=None, max_length=1_000_000)


class NoteVersionInput(BaseModel):
    version: int = Field(ge=1)


class ActionPreviewInput(BaseModel):
    planner_item_id: int | None = None
    state: bool | None = None
    planner_note_id: int | None = None
    title: str | None = Field(default=None, max_length=255)
    details: str | None = Field(default=None, max_length=10_000)
    todo_date: datetime | None = None
    course_id: int | None = None


class ActionExecuteInput(BaseModel):
    confirm_token: str = Field(min_length=20, max_length=200)


class ChatInput(BaseModel):
    messages: list[dict[str, Any]] | None = Field(default=None, max_length=50)
    message: str | None = Field(default=None, min_length=1, max_length=50_000)
    context: dict[str, Any] = Field(default_factory=dict)
    stream: bool = False

    @model_validator(mode="after")
    def require_content(self):
        if not self.messages and not self.message:
            raise ValueError("message or messages is required")
        return self


# Longest the app waits for cancelled background work before disposing the
# engine and exiting anyway.
SHUTDOWN_GRACE_SECONDS = 5.0


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def course_out(row: Course) -> dict[str, Any]:
    term = row.raw.get("term") or {}
    return {
        "id": row.canvas_id,
        "name": row.name,
        "course_code": row.course_code,
        "workflow_state": row.workflow_state,
        "term": term,
        "term_name": term.get("name"),
        "start_at": row.raw.get("start_at") or term.get("start_at"),
        "end_at": row.raw.get("end_at") or term.get("end_at"),
        "synced_at": iso(row.synced_at),
    }


def assignment_completed(row: Assignment) -> bool:
    submission = row.raw.get("submission") or {}
    return bool(
        submission.get("excused")
        or submission.get("submitted_at")
        or submission.get("graded_at")
        or submission.get("workflow_state")
        in {"submitted", "graded", "pending_review", "complete"}
    )


def planner_completed(row: PlannerItem) -> bool:
    override = row.raw.get("planner_override") or {}
    submissions = row.raw.get("submissions") or {}
    return bool(
        row.completed
        or override.get("marked_complete")
        or submissions.get("submitted")
        or submissions.get("graded")
        or submissions.get("excused")
    )


async def current_course_ids(
    session: AsyncSession, user_id: str, settings: Settings
) -> set[int]:
    """Infer the current teaching period when Canvas omits term dates."""
    start = settings.current_term_start()
    rows = (
        await session.scalars(
            select(Assignment).where(
                Assignment.user_id == user_id, Assignment.due_at.is_not(None)
            )
        )
    ).all()
    if not rows:
        return set(
            (
                await session.scalars(
                    select(Course.id).where(Course.user_id == user_id)
                )
            ).all()
        )
    counts: dict[int, list[int]] = {}
    for row in rows:
        due_at = row.due_at
        if due_at is None:
            continue
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)
        bucket = counts.setdefault(row.course_id, [0, 0])
        bucket[0 if due_at >= start else 1] += 1
    return {
        course_id
        for course_id, (current_count, old_count) in counts.items()
        if current_count > 0 and current_count >= old_count
    }


def assignment_out(
    row: Assignment, *, course_canvas_id: int | None = None
) -> dict[str, Any]:
    submission = row.raw.get("submission") or {}
    return {
        "id": row.canvas_id,
        "course_id": course_canvas_id if course_canvas_id is not None else row.course_id,
        "name": row.name,
        "due_at": iso(row.due_at),
        "html_url": row.html_url,
        "description_html": row.description_html,
        "description": row.description_html,
        "points_possible": row.points_possible,
        "submission_types": row.raw.get("submission_types") or [],
        "allowed_attempts": row.raw.get("allowed_attempts"),
        "rubric": row.raw.get("rubric") or [],
        "attachments": row.raw.get("attachments") or [],
        "submission": submission,
        "completed": assignment_completed(row),
        "missing": bool(submission.get("missing")),
        "late": bool(submission.get("late")),
        "score": submission.get("score"),
    }


def todo_out(row: LocalTodo) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "due_at": iso(row.due_at),
        "completed": row.completed,
        "created_at": iso(row.created_at),
    }


def note_out(row: Note) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "markdown": row.markdown,
        "version": row.version,
        "course_id": row.course_id,
        "assignment_id": row.assignment_id,
        "document_id": row.document_id,
        "chunk_id": row.chunk_id,
        "page": row.page,
        "slide": row.slide,
        "deleted_at": iso(row.deleted_at),
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
        "kind": "markdown_note",
    }


def document_out(row: Document, *, course_canvas_id: int | None = None) -> dict[str, Any]:
    available = bool(row.local_path)
    mime = (
        row.mime_type
        or (mimetypes.guess_type(row.title)[0] if row.title else None)
        or ""
    ).lower()
    active = mime in {
        "text/html", "application/xhtml+xml", "image/svg+xml",
        "application/javascript", "text/javascript",
    }
    previewable = bool(
        row.preview_html
        or row.content
        or mime == "application/pdf"
        or (mime.startswith("image/") and not active)
    )
    return {
        "id": row.id,
        "course_id": course_canvas_id,
        "title": row.title,
        "source_url": row.source_url,
        "kind": row.kind,
        "mime_type": row.mime_type,
        "size": row.size,
        "sha256": row.sha256,
        "version": row.version,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "canvas_file_id": row.canvas_file_id,
        "metadata": row.metadata_json or {},
        "local_path": row.local_path,
        "available_offline": available,
        "has_preview": previewable and not active,
        "snippet": row.content[:300],
        "source_updated_at": iso(row.source_updated_at),
        "downloaded_at": iso(row.downloaded_at),
        "extracted_at": iso(row.extracted_at),
        "updated_at": iso(row.updated_at),
    }


def visible_material_conditions() -> tuple[Any, ...]:
    """Hide decorative image assets from the study-material browser."""
    mime = func.lower(func.coalesce(Document.mime_type, ""))
    title = func.lower(Document.title)
    image_extensions = ("png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "tif", "tiff", "heic")
    return (
        ~mime.like("image/%"),
        *(~title.like(f"%.{extension}") for extension in image_extensions),
    )


def _resolve_frontend_dir(settings: Settings) -> Path:
    """Locate the built frontend for however this code is installed.

    An explicit setting always wins. Otherwise the layouts differ: a source
    checkout builds into ``frontend/dist`` at the repository root, a frozen
    desktop bundle unpacks it into ``_MEIPASS``, and the server image copies it
    next to the working directory while the package itself lives in site-
    packages — where the repository root two levels up holds no frontend at
    all. Resolving from that one layout served a 404 for every page in the
    container. Probe each and take the first that really has an ``index.html``;
    falling back to the checkout path keeps the "not built yet" 404 unchanged.
    """
    if settings.frontend_dir is not None:
        return settings.frontend_dir.expanduser().resolve()
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS")) / "frontend" / "dist")
    checkout = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    candidates.append(checkout)
    candidates.append(Path.cwd() / "frontend" / "dist")
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate.resolve()
    return checkout.resolve()


def create_app(
    settings: Settings | None = None,
    *,
    canvas_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()
    engine = make_engine(settings.database_url, settings)
    sessions = make_session_factory(engine)
    background_tasks: set[asyncio.Task[Any]] = set()

    def spawn(coroutine) -> None:
        """Run a background coroutine, holding a reference so it is not GC'd."""
        task = asyncio.create_task(coroutine)
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
    if settings.allow_development_secret_file:
        secret_store = RestrictedFileSecretStore(settings.data_dir / "secrets.json")
    else:
        service_suffix = hashlib.sha256(
            str(settings.data_dir.expanduser().resolve()).encode()
        ).hexdigest()[:12]
        secret_store = MigratingKeyringSecretStore(
            KeyringSecretStore(f"{settings.keyring_service}:{service_suffix}"),
            settings.data_dir / "secrets.json",
        )
    email_backend = make_email_backend(settings)
    auth = AuthService(settings, email_backend)
    credentials = CredentialVault(
        mode=settings.deployment_mode,
        desktop_store=secret_store,
        encryption_key=settings.credential_encryption_key,
        key_version=settings.credential_key_version,
    )
    canvas_clients = CanvasClientPool(transport=canvas_transport)

    async def worker_canvas_for(user_id: str) -> CanvasClient:
        async with sessions() as session:
            token = await credentials.get(session, user_id, "canvas_token")
            if not token:
                raise RuntimeError("Canvas token is not configured")
            base_url = (
                await credentials.get(session, user_id, "canvas_base_url")
                or str(settings.canvas_base_url)
            )
            return await canvas_clients.get(user_id, base_url, token)

    sync_queue = SyncJobQueue(
        sessions,
        worker_canvas_for,
        materials_root=settings.data_dir / "materials",
        poll_seconds=settings.worker_poll_seconds,
        event_poll_seconds=settings.sync_event_poll_seconds,
        settings=settings,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if settings.database_url.endswith("/:memory:"):
            await init_db(engine)
        else:
            await migrate_database(settings.database_url)
            if engine.dialect.name == "sqlite":
                async with engine.begin() as connection:
                    await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
                    await connection.exec_driver_sql("PRAGMA busy_timeout=5000")
        async with sessions() as session:
            await backfill_search_chunks(session)
        if settings.deployment_mode == "local_desktop":
            # In server mode the worker process owns both of these; running
            # them here too would just duplicate the work.
            sync_queue.start()
            spawn(
                backfill_material_extractions_later(
                    sessions, settings.data_dir / "materials"
                )
            )
            spawn(retention_loop(sessions, settings))
        yield
        pending = list(background_tasks)
        for task in pending:
            task.cancel()
        if pending:
            # A task cancelled mid-query can be slow, or stuck, returning its
            # connection to the pool. Shutdown must not hang on that: the
            # engine is disposed below either way.
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=SHUTDOWN_GRACE_SECONDS,
                )
        await sync_queue.stop()
        await canvas_clients.close()
        await engine.dispose()

    app = FastAPI(
        title="Canvas Helper",
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(LocalAccessMiddleware, settings=settings)
    app.state.settings = settings
    app.state.session_factory = sessions
    app.state.secret_store = secret_store
    app.state.credentials = credentials
    app.state.email_backend = email_backend
    app.state.auth = auth
    app.state.canvas = canvas_clients
    app.state.canvas_transport = canvas_transport
    app.state.sync_queue = sync_queue

    async def register_canvas_account(
        session: AsyncSession,
        user: User,
        base_url: str,
        profile: dict[str, Any],
    ) -> None:
        canvas_user_id = str(profile.get("id"))
        account = await session.scalar(
            select(CanvasAccount).where(
                CanvasAccount.user_id == user.id,
                CanvasAccount.base_url == base_url,
                CanvasAccount.canvas_user_id == canvas_user_id,
            )
        )
        if account is None:
            account = CanvasAccount(
                user_id=user.id,
                base_url=base_url,
                canvas_user_id=canvas_user_id,
            )
            session.add(account)
        account.display_name = profile.get("name")
        account.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await sync_queue.ensure_schedules(user.id)

    app.state.frontend_dir = _resolve_frontend_dir(settings)

    async def canvas_for(
        request: Request, user: User, session: AsyncSession
    ) -> CanvasClient:
        token = await request.app.state.credentials.get(
            session, user.id, "canvas_token"
        )
        if not token:
            raise HTTPException(status_code=503, detail="Canvas token is not configured")
        base_url = (
            await request.app.state.credentials.get(
                session, user.id, "canvas_base_url"
            )
            or str(settings.canvas_base_url)
        )
        return await request.app.state.canvas.get(user.id, base_url, token)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/auth/request-link", status_code=202)
    async def request_magic_link(
        body: MagicLinkRequest,
        request: Request,
        background: BackgroundTasks,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, str]:
        if settings.deployment_mode != "server":
            raise HTTPException(status_code=404, detail="Not found")
        await auth.request_link(
            session, body.email, request=request, background=background
        )
        return {"status": "sent"}

    @app.post("/api/auth/verify")
    async def verify_magic_link(
        body: MagicLinkVerify,
        response: Response,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        if settings.deployment_mode != "server":
            raise HTTPException(status_code=404, detail="Not found")
        user = await auth.verify(session, response, body.token)
        return {
            "authenticated": True,
            "user": {"id": user.id, "email": user.email, "name": user.display_name},
        }

    @app.get("/api/auth/me")
    async def auth_me(
        request: Request,
        response: Response,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        if settings.deployment_mode == "local_desktop":
            return {"authenticated": True, "mode": "local_desktop"}
        try:
            user, _ = await auth.authenticate(
                request, response, session, require_csrf=False
            )
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
            return {
                "authenticated": False,
                "auth_required": True,
                "mode": "server",
            }
        return {
            "authenticated": True,
            "mode": "server",
            "user": {"id": user.id, "email": user.email, "name": user.display_name},
        }

    @app.post("/api/auth/logout", status_code=204)
    async def logout(
        request: Request,
        response: Response,
        session: AsyncSession = Depends(session_dependency),
    ) -> Response:
        if settings.deployment_mode != "server":
            raise HTTPException(status_code=404, detail="Not found")
        await auth.logout(request, response, session)
        response.status_code = 204
        return response

    @app.get("/api/doctor")
    async def doctor(
        request: Request,
        user: CurrentUser,
        check_canvas: bool = False,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        await session.scalar(
            select(func.count()).select_from(Course).where(Course.user_id == user.id)
        )
        configured = bool(
            await request.app.state.credentials.get(
                session, user.id, "canvas_token"
            )
        )
        provider = (
            await request.app.state.credentials.get(session, user.id, "ai_provider")
            or "anthropic"
        )
        ai_key = await request.app.state.credentials.get(
            session, user.id, f"{provider}_api_key"
        )
        ai_model = await request.app.state.credentials.get(
            session, user.id, f"{provider}_model"
        )
        result: dict[str, Any] = {
            "database": "ok",
            "canvas_token_configured": configured,
            "canvas": "not_checked",
            "ai": (
                "available"
                if AIService(ai_key, ai_model, provider).available
                else "disabled"
            ),
        }
        if check_canvas and configured:
            try:
                profile = await (await canvas_for(request, user, session)).get_json(
                    "/api/v1/users/self/profile"
                )
                result["canvas"] = "ok"
                result["canvas_user"] = {
                    "id": profile.get("id"),
                    "name": profile.get("name"),
                }
            except Exception as exc:
                result["canvas"] = "error"
                result["canvas_error"] = type(exc).__name__
        return result

    @app.post("/api/setup/token")
    async def set_token(
        body: TokenInput,
        request: Request,
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        canvas_url = str(body.canvas_url or settings.canvas_base_url).rstrip("/")
        candidate = CanvasClient(
            canvas_url,
            body.token,
            transport=request.app.state.canvas_transport,
            max_retries=1,
        )
        try:
            profile = await candidate.get_json("/api/v1/users/self/profile")
        finally:
            await candidate.close()
        await request.app.state.credentials.set(
            session, user.id, "canvas_token", body.token
        )
        await request.app.state.credentials.set(
            session, user.id, "canvas_base_url", canvas_url
        )
        await register_canvas_account(session, user, canvas_url, profile)
        await request.app.state.canvas.discard(user.id)
        return {
            "configured": True,
            "canvas_user": {"id": profile.get("id"), "name": profile.get("name")},
        }

    @app.get("/api/me")
    async def me(
        request: Request,
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        token = await request.app.state.credentials.get(
            session, user.id, "canvas_token"
        )
        if not token:
            return {"configured": False}
        account = await session.scalar(
            select(CanvasAccount)
            .where(CanvasAccount.user_id == user.id)
            .order_by(CanvasAccount.updated_at.desc())
            .limit(1)
        )
        if account is not None:
            return {
                "configured": True,
                "id": account.canvas_user_id,
                "name": account.display_name,
                "canvas_url": account.base_url,
            }
        profile = await (await canvas_for(request, user, session)).get_json(
            "/api/v1/users/self/profile"
        )
        canvas_url = (
            await request.app.state.credentials.get(
                session, user.id, "canvas_base_url"
            )
            or str(settings.canvas_base_url)
        ).rstrip("/")
        # Backfill accounts created before multi-user scheduling was introduced.
        await register_canvas_account(session, user, canvas_url, profile)
        return {
            "configured": True,
            "id": profile.get("id"),
            "name": profile.get("name"),
            "canvas_url": canvas_url,
        }

    @app.get("/api/courses")
    async def courses(
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> list[dict[str, Any]]:
        course_ids = await current_course_ids(session, user.id, settings)
        rows = (
            await session.scalars(
                select(Course)
                .where(Course.user_id == user.id, Course.id.in_(course_ids))
                .order_by(Course.name)
            )
        ).all()
        return [course_out(row) for row in rows]

    @app.get("/api/courses/{course_id}")
    async def course(
        course_id: int,
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        row = await session.scalar(
            select(Course).where(
                Course.user_id == user.id, Course.canvas_id == course_id
            )
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Course not found")
        assignments = (
            await session.scalars(
                select(Assignment)
                .where(
                    Assignment.user_id == user.id,
                    Assignment.course_id == row.id,
                )
                .order_by(Assignment.due_at)
            )
        ).all()
        material_count = await session.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.user_id == user.id, Document.course_id == row.id)
        )
        return {
            **course_out(row),
            "assignment_count": len(assignments),
            "material_count": material_count or 0,
            "assignments": [
                assignment_out(x, course_canvas_id=row.canvas_id) for x in assignments
            ],
        }

    @app.get("/api/assignments/{assignment_id}")
    async def assignment(
        assignment_id: int,
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        row = await session.scalar(
            select(Assignment).where(
                Assignment.user_id == user.id,
                Assignment.canvas_id == assignment_id,
            )
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Assignment not found")
        course_row = await session.scalar(
            select(Course).where(
                Course.id == row.course_id, Course.user_id == user.id
            )
        )
        return {
            **assignment_out(
                row,
                course_canvas_id=course_row.canvas_id if course_row else None,
            ),
            "course_name": course_row.name if course_row else None,
            "course_code": course_row.course_code if course_row else None,
        }

    @app.get("/api/courses/{course_id}/materials")
    async def materials(
        course_id: int,
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> list[dict[str, Any]]:
        course_row = await session.scalar(
            select(Course).where(
                Course.user_id == user.id, Course.canvas_id == course_id
            )
        )
        if course_row is None:
            raise HTTPException(status_code=404, detail="Course not found")
        rows = (
            await session.scalars(
                select(Document)
                .where(
                    Document.user_id == user.id,
                    Document.course_id == course_row.id,
                    *visible_material_conditions(),
                )
                .order_by(Document.updated_at.desc())
            )
        ).all()
        return [document_out(row, course_canvas_id=course_id) for row in rows]

    async def tenant_document(
        document_id: int, user: User, session: AsyncSession
    ) -> tuple[Document, Course | None]:
        row = await session.scalar(
            select(Document).where(
                Document.id == document_id, Document.user_id == user.id
            )
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Material not found")
        course_row = (
            await session.scalar(
                select(Course).where(
                    Course.id == row.course_id, Course.user_id == user.id
                )
            )
            if row.course_id is not None
            else None
        )
        return row, course_row

    @app.get("/api/materials")
    async def all_materials(
        user: CurrentUser,
        course_id: int | None = None,
        kind: str | None = None,
        limit: int = Query(default=200, ge=1, le=500),
        session: AsyncSession = Depends(session_dependency),
    ) -> list[dict[str, Any]]:
        current_ids = await current_course_ids(session, user.id, settings)
        courses = list(
            (
                await session.scalars(
                    select(Course).where(
                        Course.user_id == user.id,
                        Course.id.in_(current_ids),
                    )
                )
            ).all()
        )
        if course_id is not None:
            courses = [row for row in courses if row.canvas_id == course_id]
        if not courses:
            return []
        course_by_id = {row.id: row for row in courses}
        statement = (
            select(Document)
            .where(
                Document.user_id == user.id,
                Document.course_id.in_(course_by_id),
                *visible_material_conditions(),
            )
            .order_by(Document.updated_at.desc(), Document.title)
            .limit(limit)
        )
        if kind:
            statement = statement.where(
                (Document.kind == kind) | (Document.source_type == kind)
            )
        rows = list((await session.scalars(statement)).all())
        return [
            {
                **document_out(
                    row,
                    course_canvas_id=course_by_id[row.course_id].canvas_id,
                ),
                "course_name": course_by_id[row.course_id].name,
            }
            for row in rows
        ]

    def material_path(row: Document, user_id: str) -> Path:
        if not row.local_path:
            raise HTTPException(status_code=404, detail="Material has not been downloaded")
        try:
            path = MaterialStore(settings.data_dir / "materials", user_id).resolve_local_path(
                row.local_path
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Invalid material path") from exc
        if not path.is_file() or path.is_symlink():
            raise HTTPException(status_code=404, detail="Downloaded material is unavailable")
        return path

    @app.get("/api/materials/{document_id}")
    async def material_detail(
        document_id: int,
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        row, course_row = await tenant_document(document_id, user, session)
        return {
            **document_out(
                row, course_canvas_id=course_row.canvas_id if course_row else None
            ),
            "course_name": course_row.name if course_row else None,
            "content_url": f"/api/materials/{row.id}/content",
            "preview_url": f"/api/materials/{row.id}/preview",
            "download_url": f"/api/materials/{row.id}/download",
        }

    @app.get("/api/materials/{document_id}/content")
    async def material_content(
        document_id: int,
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> PlainTextResponse:
        row, _ = await tenant_document(document_id, user, session)
        store = MaterialStore(settings.data_dir / "materials", user.id)
        if await refresh_document_extraction(session, row, store):
            await session.commit()
        return PlainTextResponse(
            row.content or "",
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'",
            },
        )

    @app.get("/api/materials/{document_id}/download")
    async def material_download(
        document_id: int,
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> FileResponse:
        row, _ = await tenant_document(document_id, user, session)
        path = material_path(row, user.id)
        return FileResponse(
            path,
            filename=row.title,
            media_type="application/octet-stream",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.get("/api/materials/{document_id}/preview")
    async def material_preview(
        document_id: int,
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> Response:
        row, _ = await tenant_document(document_id, user, session)
        store = MaterialStore(settings.data_dir / "materials", user.id)
        if await refresh_document_extraction(session, row, store):
            await session.commit()
        mime = (
            row.mime_type
            or (mimetypes.guess_type(row.title)[0] if row.title else None)
            or "application/octet-stream"
        ).lower()
        active = {
            "text/html", "application/xhtml+xml", "image/svg+xml",
            "application/javascript", "text/javascript",
        }
        passive_headers = {
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-cache",
        }
        if mime in active:
            raise HTTPException(status_code=415, detail="Active content preview is disabled")
        if row.preview_html:
            return HTMLResponse(
                row.preview_html,
                headers={
                    **passive_headers,
                    "Content-Security-Policy": (
                        "default-src 'none'; style-src 'unsafe-inline'; sandbox"
                    ),
                },
            )
        if mime == "application/pdf" or (
            mime.startswith("image/") and mime not in {"image/svg+xml"}
        ):
            return FileResponse(
                material_path(row, user.id), media_type=mime, headers=passive_headers
            )
        if mime.startswith("text/") or row.content:
            return PlainTextResponse(
                row.content or "",
                headers={
                    **passive_headers,
                    "Content-Security-Policy": "default-src 'none'; sandbox",
                },
            )
        raise HTTPException(status_code=415, detail="No safe preview is available")

    @app.get("/api/announcements")
    async def announcements(
        user: CurrentUser,
        course_id: int | None = None,
        session: AsyncSession = Depends(session_dependency),
    ) -> list[dict[str, Any]]:
        course_ids = await current_course_ids(session, user.id, settings)
        if course_id is not None:
            course_row = await session.scalar(
                select(Course).where(
                    Course.user_id == user.id, Course.canvas_id == course_id
                )
            )
            if course_row is None or course_row.id not in course_ids:
                return []
            course_ids = {course_row.id}
        rows = (
            await session.scalars(
                select(Announcement)
                .where(
                    Announcement.user_id == user.id,
                    Announcement.course_id.in_(course_ids),
                )
                .order_by(Announcement.posted_at.desc())
            )
        ).all()
        courses_by_id = {
            row.id: row
            for row in (
                await session.scalars(
                    select(Course).where(
                        Course.user_id == user.id, Course.id.in_(course_ids)
                    )
                )
            ).all()
        }
        return [
            {
                "id": row.canvas_id,
                "course_id": (
                    courses_by_id[row.course_id].canvas_id
                    if row.course_id in courses_by_id
                    else None
                ),
                "course_name": (
                    courses_by_id[row.course_id].name
                    if row.course_id in courses_by_id
                    else None
                ),
                "title": row.title,
                "message_html": row.message_html,
                "posted_at": iso(row.posted_at),
                "author_name": row.author_name,
                "html_url": row.html_url,
                "read_state": row.read_state,
                "unread": row.read_state == "unread",
                "attachments": [
                    {
                        key: attachment.get(key)
                        for key in (
                            "id",
                            "display_name",
                            "filename",
                            "size",
                            "content-type",
                        )
                    }
                    for attachment in (row.raw.get("attachments") or [])
                ],
            }
            for row in rows
        ]

    @app.get("/api/deadlines")
    async def deadlines(
        user: CurrentUser,
        include_completed: bool = False,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        term_start = settings.current_term_start()
        course_ids = await current_course_ids(session, user.id, settings)
        all_assignments = (
            await session.scalars(
                select(Assignment)
                .where(
                    Assignment.due_at.is_not(None),
                    Assignment.due_at >= term_start,
                    Assignment.user_id == user.id,
                    Assignment.course_id.in_(course_ids),
                )
                .order_by(Assignment.due_at)
            )
        ).all()
        assignments = [
            row
            for row in all_assignments
            if include_completed or not assignment_completed(row)
        ]
        all_planner = (
            await session.scalars(
                select(PlannerItem)
                .where(
                    PlannerItem.due_at.is_not(None),
                    PlannerItem.due_at >= term_start,
                    PlannerItem.user_id == user.id,
                    PlannerItem.course_id.in_(course_ids),
                )
                .order_by(PlannerItem.due_at)
            )
        ).all()
        planner = [
            row
            for row in all_planner
            if row.item_type in {"assignment", "quiz", "discussion_topic"}
            and (include_completed or not planner_completed(row))
        ]
        course_ids = {
            row.course_id for row in [*assignments, *planner] if row.course_id
        }
        course_rows = (
            await session.scalars(
                select(Course).where(
                    Course.user_id == user.id, Course.id.in_(course_ids)
                )
            )
        ).all()
        courses_by_id = {row.id: row for row in course_rows}
        assignment_items = [
            {
                **assignment_out(
                    row,
                    course_canvas_id=(
                        courses_by_id[row.course_id].canvas_id
                        if row.course_id in courses_by_id
                        else None
                    ),
                ),
                "assignment_id": row.canvas_id,
                "title": row.name,
                "type": "assignment",
                "course_name": (
                    courses_by_id[row.course_id].name
                    if row.course_id in courses_by_id
                    else None
                ),
            }
            for row in assignments
        ]
        planner_items = [
            {
                "id": row.id,
                "title": row.title,
                "course_id": (
                    courses_by_id[row.course_id].canvas_id
                    if row.course_id in courses_by_id
                    else None
                ),
                "due_at": iso(row.due_at),
                "completed": planner_completed(row),
                "read": row.read,
                "type": row.item_type,
            }
            for row in planner
        ]
        todo_rows = (
            await session.scalars(
                select(LocalTodo)
                .where(
                    LocalTodo.user_id == user.id, LocalTodo.due_at.is_not(None)
                )
                .order_by(LocalTodo.due_at)
            )
        ).all()
        todo_items = [
            {**todo_out(row), "type": "local_todo"}
            for row in todo_rows
            if include_completed or not row.completed
        ]
        return {
            "items": sorted(
                [*assignment_items, *todo_items],
                key=lambda item: item["due_at"] or "9999",
            ),
            "assignments": assignment_items,
            "planner": planner_items,
        }

    @app.get("/api/dashboard")
    async def dashboard(
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        course_ids = await current_course_ids(session, user.id, settings)
        counts = {
            "courses": len(course_ids),
            "assignments": await session.scalar(
                select(func.count()).select_from(Assignment)
                .where(Assignment.user_id == user.id)
            ),
            "open_todos": await session.scalar(
                select(func.count())
                .select_from(LocalTodo)
                .where(
                    LocalTodo.user_id == user.id,
                    LocalTodo.completed.is_(False),
                )
            ),
        }
        upcoming_rows = (
            await session.scalars(
                select(Assignment)
                .where(
                    Assignment.due_at >= now,
                    Assignment.user_id == user.id,
                    Assignment.course_id.in_(course_ids),
                )
                .order_by(Assignment.due_at)
                .limit(10)
            )
        ).all()
        upcoming = [row for row in upcoming_rows if not assignment_completed(row)]
        course_canvas_ids = {
            row.id: row.canvas_id
            for row in (
                await session.scalars(
                    select(Course).where(
                        Course.user_id == user.id, Course.id.in_(course_ids)
                    )
                )
            ).all()
        }
        return {
            "counts": counts,
            "upcoming": [
                assignment_out(
                    row, course_canvas_id=course_canvas_ids.get(row.course_id)
                )
                for row in upcoming
            ],
        }

    @app.get("/api/todos")
    async def todos(
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> list[dict[str, Any]]:
        rows = (
            await session.scalars(
                select(LocalTodo)
                .where(LocalTodo.user_id == user.id)
                .order_by(LocalTodo.completed, LocalTodo.due_at)
            )
        ).all()
        return [todo_out(row) for row in rows]

    @app.post("/api/todos", status_code=201)
    async def create_todo(
        body: TodoInput,
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        row = LocalTodo(user_id=user.id, title=body.title.strip(), due_at=body.due_at)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return todo_out(row)

    @app.patch("/api/todos/{todo_id}")
    async def patch_todo(
        todo_id: int,
        body: TodoPatch,
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        row = await session.scalar(
            select(LocalTodo).where(
                LocalTodo.id == todo_id, LocalTodo.user_id == user.id
            )
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Todo not found")
        for key, value in body.model_dump(exclude_unset=True).items():
            if key == "title":
                # An explicit null is a client mistake, not a request to clear
                # a non-nullable column.
                if value is None:
                    raise HTTPException(status_code=422, detail="Title cannot be null")
                value = value.strip()
            setattr(row, key, value)
        await session.commit()
        return todo_out(row)

    @app.delete("/api/todos/{todo_id}", status_code=204)
    async def delete_todo(
        todo_id: int,
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> Response:
        row = await session.scalar(
            select(LocalTodo).where(
                LocalTodo.id == todo_id, LocalTodo.user_id == user.id
            )
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Todo not found")
        await session.delete(row)
        await session.commit()
        return Response(status_code=204)

    async def note_targets(
        body: NoteInput, user_id: str, session: AsyncSession
    ) -> dict[str, int | None]:
        targets: dict[str, int | None] = {
            "course_id": None, "assignment_id": None,
            "document_id": None, "chunk_id": None,
        }
        course_row = None
        if body.course_id is not None:
            course_row = await session.scalar(
                select(Course).where(
                    Course.user_id == user_id, Course.canvas_id == body.course_id
                )
            )
            if course_row is None:
                raise HTTPException(status_code=422, detail="Note course target is not owned by this user")
            targets["course_id"] = course_row.id
        if body.assignment_id is not None:
            assignment_row = await session.scalar(
                select(Assignment).where(
                    Assignment.user_id == user_id,
                    Assignment.canvas_id == body.assignment_id,
                )
            )
            if assignment_row is None:
                raise HTTPException(status_code=422, detail="Note assignment target is not owned by this user")
            if course_row is not None and assignment_row.course_id != course_row.id:
                raise HTTPException(status_code=422, detail="Assignment does not belong to the selected course")
            targets["assignment_id"] = assignment_row.id
            targets["course_id"] = assignment_row.course_id
        document_row = None
        if body.document_id is not None:
            document_row = await session.scalar(
                select(Document).where(
                    Document.user_id == user_id, Document.id == body.document_id
                )
            )
            if document_row is None:
                raise HTTPException(status_code=422, detail="Note document target is not owned by this user")
            if targets["course_id"] is not None and document_row.course_id != targets["course_id"]:
                raise HTTPException(status_code=422, detail="Document does not belong to the selected course")
            targets["document_id"] = document_row.id
            targets["course_id"] = targets["course_id"] or document_row.course_id
        if body.chunk_id is not None:
            chunk_row = await session.scalar(
                select(DocChunk).where(
                    DocChunk.user_id == user_id, DocChunk.id == body.chunk_id
                )
            )
            if chunk_row is None:
                raise HTTPException(status_code=422, detail="Note search/citation target is not owned by this user")
            if document_row is not None and chunk_row.document_id != document_row.id:
                raise HTTPException(status_code=422, detail="Citation does not belong to the selected document")
            if targets["course_id"] is not None and chunk_row.course_id != targets["course_id"]:
                raise HTTPException(status_code=422, detail="Citation does not belong to the selected course")
            targets["chunk_id"] = chunk_row.id
            targets["document_id"] = targets["document_id"] or chunk_row.document_id
            targets["course_id"] = targets["course_id"] or chunk_row.course_id
        if (body.page is not None or body.slide is not None) and not (
            body.document_id is not None or body.chunk_id is not None
        ):
            raise HTTPException(status_code=422, detail="Page or slide requires a document or citation target")
        return targets

    async def tenant_note(
        note_id: int, user_id: str, session: AsyncSession
    ) -> Note:
        row = await session.scalar(
            select(Note).where(Note.id == note_id, Note.user_id == user_id)
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Note not found")
        return row

    def add_note_revision(session: AsyncSession, row: Note, reason: str) -> None:
        session.add(NoteRevision(
            user_id=row.user_id, note_id=row.id, version=row.version,
            title=row.title, markdown=row.markdown, reason=reason,
        ))

    @app.get("/api/notes")
    async def notes(
        user: CurrentUser,
        include_deleted: bool = False,
        course_id: int | None = None,
        assignment_id: int | None = None,
        document_id: int | None = None,
        chunk_id: int | None = None,
        session: AsyncSession = Depends(session_dependency),
    ) -> list[dict[str, Any]]:
        statement = select(Note).where(Note.user_id == user.id)
        if not include_deleted:
            statement = statement.where(Note.deleted_at.is_(None))
        if course_id is not None:
            course_row = await session.scalar(select(Course).where(
                Course.user_id == user.id, Course.canvas_id == course_id
            ))
            if course_row is None:
                return []
            statement = statement.where(Note.course_id == course_row.id)
        if assignment_id is not None:
            assignment_row = await session.scalar(select(Assignment).where(
                Assignment.user_id == user.id, Assignment.canvas_id == assignment_id
            ))
            if assignment_row is None:
                return []
            statement = statement.where(Note.assignment_id == assignment_row.id)
        if document_id is not None:
            statement = statement.where(Note.document_id == document_id)
        if chunk_id is not None:
            statement = statement.where(Note.chunk_id == chunk_id)
        rows = (await session.scalars(statement.order_by(Note.updated_at.desc()))).all()
        return [note_out(row) for row in rows]

    @app.post("/api/notes", status_code=201)
    async def create_note(
        body: NoteInput,
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        targets = await note_targets(body, user.id, session)
        row = Note(
            user_id=user.id, title=body.title.strip(), markdown=body.markdown,
            page=body.page, slide=body.slide, **targets,
        )
        session.add(row)
        await session.flush()
        add_note_revision(session, row, "create")
        await session.commit()
        await session.refresh(row)
        return note_out(row)

    @app.get("/api/notes/{note_id}")
    async def get_note(
        note_id: int, user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        return note_out(await tenant_note(note_id, user.id, session))

    @app.patch("/api/notes/{note_id}")
    async def patch_note(
        note_id: int, body: NotePatch, user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        row = await tenant_note(note_id, user.id, session)
        if row.deleted_at is not None:
            raise HTTPException(status_code=409, detail={"message": "Note is deleted", "note": note_out(row)})
        if row.version != body.version:
            raise HTTPException(status_code=409, detail={"message": "Note changed elsewhere", "note": note_out(row)})
        changes = body.model_dump(exclude_unset=True, exclude={"version"})
        if any(changes.get(key) is None for key in ("title", "markdown") if key in changes):
            raise HTTPException(
                status_code=422, detail="Title and markdown cannot be null"
            )
        if "title" in changes:
            changes["title"] = changes["title"].strip()
        if not changes or all(getattr(row, key) == value for key, value in changes.items()):
            return note_out(row)
        next_values = {
            "title": changes.get("title", row.title),
            "markdown": changes.get("markdown", row.markdown),
            "version": body.version + 1,
            "updated_at": datetime.now(timezone.utc),
        }
        session.expunge(row)
        result = await session.execute(
            update(Note).where(
                Note.id == note_id,
                Note.user_id == user.id,
                Note.version == body.version,
                Note.deleted_at.is_(None),
            ).values(**next_values)
        )
        if result.rowcount != 1:
            await session.rollback()
            current = await tenant_note(note_id, user.id, session)
            raise HTTPException(status_code=409, detail={"message": "Note changed elsewhere", "note": note_out(current)})
        row = await tenant_note(note_id, user.id, session)
        add_note_revision(session, row, "autosave")
        await session.commit()
        await session.refresh(row)
        return note_out(row)

    @app.delete("/api/notes/{note_id}")
    async def delete_note(
        note_id: int, body: NoteVersionInput, user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        row = await tenant_note(note_id, user.id, session)
        if row.version != body.version:
            raise HTTPException(status_code=409, detail={"message": "Note changed elsewhere", "note": note_out(row)})
        if row.deleted_at is None:
            changed_at = datetime.now(timezone.utc)
            session.expunge(row)
            result = await session.execute(update(Note).where(
                Note.id == note_id, Note.user_id == user.id,
                Note.version == body.version, Note.deleted_at.is_(None),
            ).values(
                deleted_at=changed_at, updated_at=changed_at,
                version=body.version + 1,
            ))
            if result.rowcount != 1:
                await session.rollback()
                current = await tenant_note(note_id, user.id, session)
                raise HTTPException(status_code=409, detail={"message": "Note changed elsewhere", "note": note_out(current)})
            row = await tenant_note(note_id, user.id, session)
            add_note_revision(session, row, "delete")
            await session.commit()
        return note_out(row)

    @app.post("/api/notes/{note_id}/restore")
    async def restore_note(
        note_id: int, body: NoteVersionInput, user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        row = await tenant_note(note_id, user.id, session)
        if row.version != body.version:
            raise HTTPException(status_code=409, detail={"message": "Note changed elsewhere", "note": note_out(row)})
        if row.deleted_at is not None:
            session.expunge(row)
            result = await session.execute(update(Note).where(
                Note.id == note_id, Note.user_id == user.id,
                Note.version == body.version, Note.deleted_at.is_not(None),
            ).values(
                deleted_at=None, updated_at=datetime.now(timezone.utc),
                version=body.version + 1,
            ))
            if result.rowcount != 1:
                await session.rollback()
                current = await tenant_note(note_id, user.id, session)
                raise HTTPException(status_code=409, detail={"message": "Note changed elsewhere", "note": note_out(current)})
            row = await tenant_note(note_id, user.id, session)
            add_note_revision(session, row, "restore")
            await session.commit()
        return note_out(row)

    @app.get("/api/notes/{note_id}/revisions")
    async def note_revisions(
        note_id: int, user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> list[dict[str, Any]]:
        await tenant_note(note_id, user.id, session)
        rows = (await session.scalars(
            select(NoteRevision).where(
                NoteRevision.note_id == note_id, NoteRevision.user_id == user.id
            ).order_by(NoteRevision.version.desc())
        )).all()
        return [{
            "id": row.id, "version": row.version, "title": row.title,
            "markdown": row.markdown, "reason": row.reason,
            "created_at": iso(row.created_at),
        } for row in rows]

    @app.post("/api/notes/{note_id}/revisions/{revision_version}/restore")
    async def restore_note_revision(
        note_id: int, revision_version: int, body: NoteVersionInput,
        user: CurrentUser, session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        row = await tenant_note(note_id, user.id, session)
        if row.version != body.version:
            raise HTTPException(status_code=409, detail={"message": "Note changed elsewhere", "note": note_out(row)})
        revision = await session.scalar(select(NoteRevision).where(
            NoteRevision.note_id == note_id,
            NoteRevision.user_id == user.id,
            NoteRevision.version == revision_version,
        ))
        if revision is None:
            raise HTTPException(status_code=404, detail="Revision not found")
        row.title, row.markdown = revision.title, revision.markdown
        row.deleted_at = None
        row.version += 1
        row.updated_at = datetime.now(timezone.utc)
        add_note_revision(session, row, "history_restore")
        await session.commit()
        return note_out(row)

    @app.get("/api/notes/{note_id}/export")
    async def export_note(
        note_id: int, user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> PlainTextResponse:
        row = await tenant_note(note_id, user.id, session)
        safe_name = "".join(c for c in row.title if c.isalnum() or c in " -_").strip() or "note"
        return PlainTextResponse(
            f"# {row.title}\n\n{row.markdown}",
            media_type="text/markdown",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_name[:100]}.md"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/search")
    async def search(
        user: CurrentUser,
        q: str = Query(min_length=1, max_length=200),
        course_id: int | None = None,
        kind: list[str] = Query(default=[]),
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = Query(default=30, ge=1, le=100),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        internal_course_id: int | None = None
        if course_id is not None:
            course_row = await session.scalar(
                select(Course).where(
                    Course.user_id == user.id, Course.canvas_id == course_id
                )
            )
            if course_row is None:
                return {"results": [], "groups": {}}
            internal_course_id = course_row.id
        rows = await SearchService().search(
            session,
            user.id,
            q,
            filters=SearchFilters(
                course_id=internal_course_id,
                kinds=tuple(kind),
                date_from=date_from,
                date_to=date_to,
            ),
            top_k=limit,
        )
        course_rows = (
            await session.scalars(
                select(Course).where(
                    Course.user_id == user.id,
                    Course.id.in_({row["course_id"] for row in rows if row["course_id"]}),
                )
            )
        ).all()
        courses_by_id = {
            row.id: {"id": row.canvas_id, "name": row.name}
            for row in course_rows
        }
        results = [
            {
                **row,
                "result_type": row["source_kind"],
                "course": courses_by_id.get(row["course_id"]),
                "preview_url": (
                    f"/materials/{row['document_id']}?chunk={row['id']}"
                    if row["document_id"] is not None
                    else None
                ),
            }
            for row in rows
        ]
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in results:
            groups.setdefault(str(row["source_kind"]), []).append(row)
        return {
            "query": q,
            "results": results,
            "groups": groups,
        }

    @app.post("/api/sync/{job}", status_code=202)
    async def sync(
        job: str,
        user: CurrentUser,
        wait: bool = Query(False),
        await_completion: bool = Query(False),
        await_job: bool = Query(False, alias="await"),
        timeout: float = Query(30, ge=0.1, le=120),
    ) -> dict[str, Any]:
        # A held-open request is a cheap way to tie up a worker, so the caller's
        # timeout is clamped to the configured server-side ceiling.
        timeout = min(timeout, settings.sync_wait_max_seconds)
        try:
            row, created = await sync_queue.enqueue(user.id, job)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if wait or await_completion or await_job:
            try:
                row = await sync_queue.wait(user.id, row.id, timeout)
            except TimeoutError as exc:
                raise HTTPException(
                    status_code=504, detail="Timed out waiting for sync job"
                ) from exc
        return {"job_id": row.id, "deduplicated": not created, "job": job_out(row)}

    @app.get("/api/sync/status")
    async def sync_status(
        user: CurrentUser,
    ) -> dict[str, Any]:
        return await sync_queue.status(user.id)

    @app.get("/api/sync/jobs/{job_id}")
    async def sync_job(job_id: str, user: CurrentUser) -> dict[str, Any]:
        async with sessions() as session:
            row = await session.scalar(
                select(SyncJob).where(
                    SyncJob.id == job_id, SyncJob.user_id == user.id
                )
            )
        if row is None:
            raise HTTPException(status_code=404, detail="Sync job not found")
        return job_out(row)

    @app.delete("/api/sync/jobs/{job_id}")
    async def cancel_sync_job(job_id: str, user: CurrentUser) -> dict[str, Any]:
        row = await sync_queue.cancel(user.id, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Sync job not found")
        return job_out(row)

    @app.get("/api/sync/events")
    async def sync_events(user: CurrentUser) -> StreamingResponse:
        return StreamingResponse(
            sync_queue.event_stream(user.id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/actions/{action}/preview")
    async def preview_action(
        action: str,
        body: ActionPreviewInput,
        request: Request,
        user: CurrentUser,
    ) -> dict[str, Any]:
        # Previews are computed from local state only; nothing is sent to Canvas
        # until the confirmation token is executed.
        service = ActionService(
            sessions, settings.action_token_ttl_seconds, user.id, None
        )
        try:
            return await service.preview(action, body.model_dump())
        except ActionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/actions/{action}/execute")
    async def execute_action(
        action: str,
        body: ActionExecuteInput,
        request: Request,
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        service = ActionService(
            sessions,
            settings.action_token_ttl_seconds,
            user.id,
            (
                await canvas_for(request, user, session)
                if action == "planner.complete" or action.startswith("planner.note.")
                else None
            ),
        )
        try:
            return await service.execute(action, body.confirm_token)
        except ActionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.put("/api/settings/ai")
    async def set_ai_settings(
        body: AISettingsInput,
        request: Request,
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        await request.app.state.credentials.set(
            session, user.id, f"{body.provider}_api_key", body.api_key
        )
        await request.app.state.credentials.set(
            session, user.id, f"{body.provider}_model", body.model
        )
        await request.app.state.credentials.set(
            session, user.id, "ai_provider", body.provider
        )
        # One provider is active at a time. A key left behind for the other one
        # would sit in the vault where the user can neither see nor remove it.
        other = "openai" if body.provider == "anthropic" else "anthropic"
        await request.app.state.credentials.delete(session, user.id, f"{other}_api_key")
        await request.app.state.credentials.delete(session, user.id, f"{other}_model")
        return {"configured": True, "provider": body.provider, "model": body.model}

    @app.get("/api/settings/ai")
    async def get_ai_settings(
        request: Request,
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        """Report whether a key is saved. The key itself never leaves the server."""
        vault = request.app.state.credentials
        provider = await vault.get(session, user.id, "ai_provider")
        api_key = await vault.get(session, user.id, f"{provider}_api_key") if provider else None
        if not provider or not api_key:
            return {"configured": False}
        return {
            "configured": True,
            "provider": provider,
            "model": await vault.get(session, user.id, f"{provider}_model"),
            # Enough to tell two saved keys apart at a glance, not enough to
            # be worth reading off a screenshot or a log.
            "key_hint": "…" + api_key[-4:],
        }

    @app.delete("/api/settings/ai", status_code=204)
    async def delete_ai_settings(
        request: Request,
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> Response:
        vault = request.app.state.credentials
        provider = await vault.get(session, user.id, "ai_provider")
        for provider_name in ({provider} if provider else {"anthropic", "openai"}):
            await vault.delete(session, user.id, f"{provider_name}_api_key")
            await vault.delete(session, user.id, f"{provider_name}_model")
        await vault.delete(session, user.id, "ai_provider")
        return Response(status_code=204)

    @app.post("/api/ai/chat")
    async def ai_chat(
        body: ChatInput,
        request: Request,
        user: CurrentUser,
        session: AsyncSession = Depends(session_dependency),
    ) -> Response:
        try:
            messages = body.messages or [{"role": "user", "content": body.message}]
            prompt = body.message or str(messages[-1].get("content", ""))
            context = body.context
            scope, scope_id = "global", None
            if context.get("assignment_id") is not None:
                scope, scope_id = "assignment", context["assignment_id"]
            elif context.get("document_id") is not None:
                scope, scope_id = "document", context["document_id"]
            elif context.get("course_id") is not None:
                course_row = await session.scalar(
                    select(Course).where(
                        Course.user_id == user.id,
                        Course.canvas_id == int(context["course_id"]),
                    )
                )
                if course_row:
                    scope, scope_id = "course", course_row.id
            sources = await RetrievalService().retrieve(
                session, user.id, prompt, scope=scope, scope_id=scope_id, top_k=8
            )
            provider = (
                await request.app.state.credentials.get(
                    session, user.id, "ai_provider"
                )
                or "anthropic"
            )
            api_key = await request.app.state.credentials.get(
                session, user.id, f"{provider}_api_key"
            )
            model = await request.app.state.credentials.get(
                session, user.id, f"{provider}_model"
            )
            # Only the single desktop owner may fall back to process-wide keys;
            # in server mode every user supplies their own.
            if user.id == LOCAL_USER_ID:
                api_key = api_key or getattr(settings, f"{provider}_api_key", None)
                model = model or getattr(settings, f"{provider}_model", None)
            service = AIService(
                api_key,
                model,
                provider,
                timeout=settings.ai_request_timeout_seconds,
            )
            wants_stream = body.stream or "text/event-stream" in request.headers.get(
                "accept", ""
            )
            if wants_stream:
                async def events():
                    async for event in service.stream(messages, sources=sources):
                        yield sse_event(event)

                return StreamingResponse(
                    events(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            result = await service.chat(messages, sources=sources)
            result["content"] = result.get("text", "")
            return Response(
                content=json.dumps(result, ensure_ascii=False),
                media_type="application/json",
            )
        except AIUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/{full_path:path}", include_in_schema=False)
    async def frontend(full_path: str, request: Request) -> Response:
        """Serve built assets and fall back to index.html for client routes."""
        root: Path = request.app.state.frontend_dir
        if not root.is_dir():
            raise HTTPException(status_code=404, detail="Frontend build is unavailable")
        requested = (root / full_path).resolve()
        try:
            requested.relative_to(root)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Not found") from exc
        if requested.is_file():
            return FileResponse(
                requested,
                headers={"Cache-Control": "public, max-age=31536000, immutable"}
                if full_path.startswith("assets/")
                else {"Cache-Control": "no-cache"},
            )
        accepts_html = "text/html" in request.headers.get("accept", "")
        if (
            full_path.startswith(("api/", "docs", "openapi.json"))
            or Path(full_path).suffix
            or not accepts_html
        ):
            raise HTTPException(status_code=404, detail="Not found")
        index = root / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404, detail="Frontend build is unavailable")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
