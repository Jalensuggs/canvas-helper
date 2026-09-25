from datetime import datetime, timezone
import logging
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .canvas.client import CanvasClient, CanvasPermissionError
from .materials import MaterialStore, extract_document
from .models import Announcement, Assignment, Course, Document, PlannerItem, SyncRun
from .search import rebuild_document_chunks
from .security import sanitize_html

logger = logging.getLogger(__name__)

_CANVAS_FILE_ID = re.compile(r"/files/(\d+)(?:/|$|[?#])")


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def datetimes_equal(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return left is right
    if left.tzinfo is None:
        left = left.replace(tzinfo=timezone.utc)
    if right.tzinfo is None:
        right = right.replace(tzinfo=timezone.utc)
    return left == right


class SyncService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        canvas: CanvasClient,
        user_id: str,
        materials_root: Path | None = None,
    ):
        self.sessions = sessions
        self.canvas = canvas
        self.user_id = user_id
        self.materials_root = materials_root or (Path.home() / ".canvas-helper" / "materials")

    async def run(self, job: str) -> dict[str, Any]:
        """Run immediately while preserving the legacy SyncRun audit record."""
        if job == "deadlines":
            job = "assignments"
        if job not in {
            "courses",
            "assignments",
            "planner",
            "materials",
            "announcements",
            "all",
        }:
            raise ValueError("Unknown sync job")
        async with self.sessions() as session:
            run = SyncRun(user_id=self.user_id, job=job, status="running")
            session.add(run)
            await session.commit()
            await session.refresh(run)
        try:
            result = await self.execute(job)
            status, detail = "success", str(result)
        except Exception as exc:
            status, detail = "failed", type(exc).__name__
            raise
        finally:
            async with self.sessions() as session:
                stored = await session.scalar(
                    select(SyncRun).where(
                        SyncRun.id == run.id, SyncRun.user_id == self.user_id
                    )
                )
                if stored:
                    stored.status = status
                    stored.detail = detail
                    stored.finished_at = datetime.now(timezone.utc)
                    await session.commit()
        return result

    async def execute(self, job: str) -> dict[str, Any]:
        """Execute sync work without creating queue or audit state."""
        jobs = {
            "courses": self.sync_courses,
            "assignments": self.sync_assignments,
            "deadlines": self.sync_assignments,
            "planner": self.sync_planner,
            "materials": self.sync_materials,
            "announcements": self.sync_announcements,
            "all": self.sync_all,
        }
        if job not in jobs:
            raise ValueError("Unknown sync job")
        return await jobs[job]()

    async def sync_all(self) -> dict[str, Any]:
        courses = await self.sync_courses()
        assignments = await self.sync_assignments()
        planner = await self.sync_planner()
        announcements = await self.sync_announcements()
        materials = await self.sync_materials()
        return {
            "courses": courses,
            "assignments": assignments,
            "planner": planner,
            "materials": materials,
            "announcements": announcements,
        }

    async def sync_courses(self) -> dict[str, int]:
        count = 0
        changed = 0
        async with self.sessions() as session:
            async for item in self.canvas.paginate(
                "/api/v1/courses", params={"enrollment_state": "active"}
            ):
                canvas_id = int(item["id"])
                course = await session.scalar(
                    select(Course).where(
                        Course.user_id == self.user_id,
                        Course.canvas_id == canvas_id,
                    )
                )
                if course is None:
                    course = Course(
                        user_id=self.user_id,
                        canvas_id=canvas_id,
                        name=item.get("name") or "Untitled",
                    )
                    session.add(course)
                    changed += 1
                elif (
                    course.name != (item.get("name") or course.name)
                    or course.course_code != item.get("course_code")
                    or course.workflow_state != item.get("workflow_state")
                    or course.raw != item
                ):
                    changed += 1
                course.name = item.get("name") or course.name
                course.course_code = item.get("course_code")
                course.workflow_state = item.get("workflow_state")
                course.raw = item
                course.synced_at = datetime.now(timezone.utc)
                count += 1
            await session.commit()
        return {"count": count, "changed": changed}

    async def sync_assignments(self) -> dict[str, int]:
        count = 0
        changed = 0
        async with self.sessions() as session:
            courses = list(
                (
                    await session.scalars(
                        select(Course).where(Course.user_id == self.user_id)
                    )
                ).all()
            )
        for course in courses:
            async with self.sessions() as session:
                async for item in self.canvas.paginate(
                    f"/api/v1/courses/{course.canvas_id}/assignments",
                    params={"include[]": "submission"},
                ):
                    canvas_id = int(item["id"])
                    assignment = await session.scalar(
                        select(Assignment).where(
                            Assignment.user_id == self.user_id,
                            Assignment.canvas_id == canvas_id,
                        )
                    )
                    if assignment is None:
                        assignment = Assignment(
                            user_id=self.user_id,
                            canvas_id=canvas_id,
                            course_id=course.id,
                            name=item.get("name") or "Untitled",
                        )
                        session.add(assignment)
                        changed += 1
                    else:
                        next_description = sanitize_html(
                            item.get("description") or ""
                        )
                        if (
                            assignment.course_id != course.id
                            or assignment.name != (item.get("name") or assignment.name)
                            or not datetimes_equal(
                                assignment.due_at, parse_datetime(item.get("due_at"))
                            )
                            or assignment.html_url != item.get("html_url")
                            or assignment.description_html != next_description
                            or assignment.points_possible != item.get("points_possible")
                            or assignment.raw != item
                        ):
                            changed += 1
                    assignment.course_id = course.id
                    assignment.name = item.get("name") or assignment.name
                    assignment.due_at = parse_datetime(item.get("due_at"))
                    assignment.html_url = item.get("html_url")
                    assignment.description_html = sanitize_html(
                        item.get("description") or ""
                    )
                    assignment.points_possible = item.get("points_possible")
                    assignment.raw = item
                    assignment.synced_at = datetime.now(timezone.utc)
                    await self._upsert_document(
                        session,
                        course_id=course.id,
                        title=assignment.name,
                        source_url=(
                            assignment.html_url
                            or f"canvas-assignment://{assignment.canvas_id}"
                        ),
                        content=assignment.description_html or "",
                        source_type="assignment",
                        source_id=str(assignment.canvas_id),
                        kind="assignment",
                        source_date=assignment.due_at,
                    )
                    count += 1
                await session.commit()
        return {"count": count, "changed": changed}

    async def sync_planner(self) -> dict[str, int]:
        count = 0
        changed = 0
        async with self.sessions() as session:
            async for item in self.canvas.paginate("/api/v1/planner/items"):
                override = item.get("planner_override") or {}
                raw_id = item.get("plannable_id") or item.get("id")
                canvas_id = f"{item.get('plannable_type', 'item')}:{raw_id}"
                existing = await session.scalar(
                    select(PlannerItem).where(PlannerItem.canvas_id == canvas_id)
                    .where(PlannerItem.user_id == self.user_id)
                )
                if existing is None:
                    existing = PlannerItem(
                        user_id=self.user_id,
                        canvas_id=canvas_id,
                        title=item.get("plannable", {}).get("title")
                        or item.get("context_name")
                        or "Untitled",
                    )
                    session.add(existing)
                    changed += 1
                elif existing.raw != item:
                    changed += 1
                canvas_course_id = item.get("course_id")
                course = (
                    await session.scalar(
                        select(Course).where(
                            Course.user_id == self.user_id,
                            Course.canvas_id == int(canvas_course_id),
                        )
                    )
                    if canvas_course_id is not None
                    else None
                )
                existing.course_id = course.id if course else None
                existing.item_type = item.get("plannable_type")
                existing.due_at = parse_datetime(
                    item.get("plannable_date")
                    or item.get("plannable", {}).get("due_at")
                )
                submissions = item.get("submissions") or {}
                existing.completed = bool(
                    override.get("marked_complete")
                    or submissions.get("submitted")
                    or submissions.get("graded")
                    or submissions.get("excused")
                )
                existing.read = bool(item.get("new_activity") is False)
                existing.raw = item
                count += 1
            await session.commit()
        return {"count": count, "changed": changed}

    async def _optional_list(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        try:
            return [
                item
                async for item in self.canvas.paginate(path, params=params)
                if isinstance(item, dict)
            ]
        except CanvasPermissionError:
            return []
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {400, 404}:
                return []
            raise

    async def _upsert_document(
        self,
        session: AsyncSession,
        *,
        course_id: int,
        title: str,
        source_url: str,
        content: str = "",
        source_type: str = "page",
        source_id: str | None = None,
        kind: str = "page",
        source_date: datetime | None = None,
    ) -> bool:
        clean_content = sanitize_html(content)
        source_id = source_id or source_url
        document = await session.scalar(
            select(Document).where(
                Document.user_id == self.user_id,
                Document.source_type == source_type,
                Document.source_id == source_id,
            )
        )
        if document is None:
            document = await session.scalar(
                select(Document).where(
                    Document.user_id == self.user_id,
                    Document.course_id == course_id,
                    Document.source_url == source_url,
                    Document.source_type.is_(None),
                )
            )
        if document is None:
            document = Document(
                user_id=self.user_id,
                course_id=course_id,
                title=title[:500] or "Untitled",
                source_url=source_url,
                source_type=source_type,
                source_id=source_id,
                kind=kind,
            )
            session.add(document)
            changed = True
        else:
            changed = (
                document.title != (title[:500] or document.title)
                or document.content != clean_content
            )
        document.title = title[:500] or document.title
        document.course_id = course_id
        document.source_url = source_url
        document.source_type = source_type
        document.source_id = source_id
        document.kind = kind
        document.content = clean_content
        document.source_updated_at = source_date or document.source_updated_at
        document.updated_at = datetime.now(timezone.utc)
        await session.flush()
        await rebuild_document_chunks(session, document)
        return changed

    async def _file_metadata(
        self, canvas_course_id: int, file_id: str, fallback: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        fallback = fallback or {}
        if fallback.get("url") and fallback.get("id"):
            return fallback
        try:
            value = await self.canvas.get_json(
                f"/api/v1/files/{file_id}"
            )
            return value if isinstance(value, dict) else None
        except (CanvasPermissionError, httpx.HTTPStatusError):
            return fallback or None

    async def _upsert_file(
        self,
        session: AsyncSession,
        store: MaterialStore,
        *,
        course_id: int,
        canvas_course_id: int,
        item: dict[str, Any],
        source_type: str,
        source_id: str,
    ) -> bool:
        raw_file_id = item.get("id") or item.get("file_id") or item.get("content_id")
        if raw_file_id is None:
            return False
        file_id = str(raw_file_id)
        metadata = await self._file_metadata(canvas_course_id, file_id, item)
        if not metadata or not metadata.get("url"):
            return False
        filename = str(
            metadata.get("display_name") or metadata.get("filename") or f"file-{file_id}"
        )
        document = await session.scalar(
            select(Document).where(
                Document.user_id == self.user_id,
                Document.source_type == "canvas_file",
                Document.source_id == file_id,
            )
        )
        created = document is None
        if document is None:
            document = Document(
                user_id=self.user_id,
                course_id=course_id,
                title=filename[:500],
                source_type="canvas_file",
                source_id=file_id,
                canvas_file_id=file_id,
                kind="file",
                source_url=str(metadata["url"]),
            )
            session.add(document)
        prior = (document.sha256, document.version or 0)
        result = await store.download(
            self.canvas,
            url=str(metadata["url"]),
            course_id=course_id,
            file_id=file_id,
            filename=filename,
            prior_sha256=document.sha256,
            prior_version=document.version or 0,
            expected_size=int(metadata["size"]) if metadata.get("size") is not None else None,
        )
        extracted = extract_document(
            result.path,
            str(metadata.get("content-type") or metadata.get("content_type") or result.mime_type or ""),
        )
        now = datetime.now(timezone.utc)
        document.course_id = course_id
        document.title = filename[:500]
        document.source_url = str(metadata["url"])
        document.local_path = store.relative(result.path)
        document.sha256 = result.sha256
        document.size = result.size
        document.mime_type = str(
            metadata.get("content-type") or metadata.get("content_type") or result.mime_type or ""
        ) or None
        document.version = result.version
        document.canvas_file_id = file_id
        document.kind = "file"
        document.content = extracted.text
        document.preview_html = extracted.preview_html
        document.source_updated_at = parse_datetime(
            metadata.get("updated_at") or metadata.get("modified_at")
        )
        document.downloaded_at = now
        document.extracted_at = now
        document.updated_at = now
        document.metadata_json = {
            "canvas": {
                key: metadata.get(key)
                for key in ("id", "uuid", "display_name", "filename", "size", "content-type", "created_at", "updated_at")
            },
            "discovered_from": {"type": source_type, "id": source_id},
            "extraction": {**extracted.metadata, "error": extracted.error},
            "preserved_path": store.relative(result.preserved_path) if result.preserved_path else None,
        }
        await session.flush()
        await rebuild_document_chunks(session, document)
        return created or prior != (result.sha256, result.version)

    async def sync_materials(self) -> dict[str, int]:
        """Index pages and securely mirror discoverable Canvas file attachments."""
        count = 0
        changed = 0
        failed = 0
        async with self.sessions() as session:
            courses = list(
                (
                    await session.scalars(
                        select(Course).where(Course.user_id == self.user_id)
                    )
                ).all()
            )

        for course in courses:
            course_id = course.id
            canvas_course_id = course.canvas_id
            async with self.sessions() as session:
                indexed_page_urls: set[str] = set()
                discovered_files: dict[str, tuple[dict[str, Any], str, str]] = {}
                assignments = (
                    await session.scalars(
                        select(Assignment).where(
                            Assignment.user_id == self.user_id,
                            Assignment.course_id == course_id,
                        )
                    )
                ).all()
                for assignment in assignments:
                    changed += int(await self._upsert_document(
                        session,
                        course_id=course_id,
                        title=assignment.name,
                        source_url=(
                            assignment.html_url
                            or f"canvas-assignment://{assignment.canvas_id}"
                        ),
                        content=assignment.description_html or "",
                        source_type="assignment",
                        source_id=str(assignment.canvas_id),
                        kind="assignment",
                        source_date=assignment.due_at,
                    ))
                    count += 1
                    for attachment in assignment.raw.get("attachments") or []:
                        if isinstance(attachment, dict) and attachment.get("id") is not None:
                            discovered_files[str(attachment["id"])] = (
                                attachment, "assignment", str(assignment.canvas_id)
                            )

                modules = await self._optional_list(
                    f"/api/v1/courses/{canvas_course_id}/modules"
                )
                for module in modules:
                    module_id = module.get("id")
                    if not module_id:
                        continue
                    items = await self._optional_list(
                        f"/api/v1/courses/{canvas_course_id}/modules/{module_id}/items"
                    )
                    for item in items:
                        item_type = item.get("type")
                        title = item.get("title") or "Untitled"
                        source_url = (
                            item.get("html_url")
                            or item.get("external_url")
                            or f"canvas-module-item://{item.get('id')}"
                        )
                        content = ""
                        if item_type == "Page" and item.get("page_url"):
                            indexed_page_urls.add(str(item["page_url"]))
                            try:
                                page = await self.canvas.get_json(
                                    f"/api/v1/courses/{canvas_course_id}/pages/"
                                    f"{item['page_url']}"
                                )
                                title = page.get("title") or title
                                source_url = page.get("html_url") or source_url
                                content = page.get("body") or ""
                                for file_id in _CANVAS_FILE_ID.findall(content):
                                    discovered_files.setdefault(
                                        file_id,
                                        ({"id": file_id}, "page", str(item["page_url"])),
                                    )
                            except (CanvasPermissionError, httpx.HTTPStatusError):
                                pass
                        if item_type == "File" and item.get("content_id") is not None:
                            file_id = str(item["content_id"])
                            discovered_files[file_id] = (
                                {"id": file_id, "display_name": title},
                                "module_item",
                                str(item.get("id") or file_id),
                            )
                        changed += int(await self._upsert_document(
                            session,
                            course_id=course_id,
                            title=title,
                            source_url=source_url,
                            content=content,
                            source_type="module_item",
                            source_id=f"{canvas_course_id}:{item.get('id') or source_url}",
                            kind=str(item_type or "module").lower(),
                        ))
                        count += 1

                pages = await self._optional_list(
                    f"/api/v1/courses/{canvas_course_id}/pages"
                )
                for page_summary in pages:
                    page_url = page_summary.get("url")
                    if not page_url or str(page_url) in indexed_page_urls:
                        continue
                    try:
                        page = await self.canvas.get_json(
                            f"/api/v1/courses/{canvas_course_id}/pages/{page_url}"
                        )
                    except (CanvasPermissionError, httpx.HTTPStatusError):
                        continue
                    changed += int(await self._upsert_document(
                        session,
                        course_id=course_id,
                        title=page.get("title") or page_summary.get("title") or "Page",
                        source_url=(
                            page.get("html_url")
                            or f"canvas-page://{canvas_course_id}/{page_url}"
                        ),
                        content=page.get("body") or "",
                        source_type="page",
                        source_id=f"{canvas_course_id}:{page_url}",
                        kind="page",
                    ))
                    count += 1
                    for file_id in _CANVAS_FILE_ID.findall(page.get("body") or ""):
                        discovered_files.setdefault(
                            file_id, ({"id": file_id}, "page", str(page_url))
                        )

                announcements = (
                    await session.scalars(
                        select(Announcement).where(
                            Announcement.user_id == self.user_id,
                            Announcement.course_id == course_id,
                        )
                    )
                ).all()
                for announcement in announcements:
                    changed += int(await self._upsert_document(
                        session,
                        course_id=course_id,
                        title=announcement.title,
                        source_url=(
                            announcement.html_url
                            or f"canvas-announcement://{announcement.canvas_id}"
                        ),
                        content=announcement.message_html or "",
                        source_type="announcement",
                        source_id=str(announcement.canvas_id),
                        kind="announcement",
                        source_date=announcement.posted_at,
                    ))
                    count += 1
                    for attachment in announcement.raw.get("attachments") or []:
                        if isinstance(attachment, dict) and attachment.get("id") is not None:
                            discovered_files[str(attachment["id"])] = (
                                attachment, "announcement", str(announcement.canvas_id)
                            )

                # Do not hold a SQLite write lock while downloading and extracting
                # potentially large files from Canvas.
                await session.commit()
                store = MaterialStore(self.materials_root, self.user_id)
                for item, source_type, source_id in discovered_files.values():
                    try:
                        changed += int(
                            await self._upsert_file(
                                session,
                                store,
                                course_id=course_id,
                                canvas_course_id=canvas_course_id,
                                item=item,
                                source_type=source_type,
                                source_id=source_id,
                            )
                        )
                        count += 1
                        await session.commit()
                    except (httpx.HTTPError, OSError, ValueError) as exc:
                        await session.rollback()
                        failed += 1
                        logger.warning(
                            "Skipping unavailable Canvas file %s: %s",
                            item.get("id") or item.get("file_id") or "unknown",
                            type(exc).__name__,
                        )
        return {"count": count, "changed": changed, "failed": failed}

    async def sync_announcements(self) -> dict[str, int]:
        changed = 0
        local_now = datetime.now(ZoneInfo("Australia/Sydney"))
        start_month = 7 if local_now.month >= 7 else 1
        start_date = f"{local_now.year}-{start_month:02d}-01"
        start_at = datetime(
            local_now.year,
            start_month,
            1,
            tzinfo=ZoneInfo("Australia/Sydney"),
        ).astimezone(timezone.utc)
        async with self.sessions() as session:
            courses = list(
                (
                    await session.scalars(
                        select(Course).where(Course.user_id == self.user_id)
                    )
                ).all()
            )
        course_by_canvas_id = {course.canvas_id: course.id for course in courses}
        course_ids = list(course_by_canvas_id)

        discovered: dict[int, dict[str, Any]] = {}
        for offset in range(0, len(course_ids), 10):
            batch = course_ids[offset : offset + 10]
            rows = await self._optional_list(
                "/api/v1/announcements",
                params={
                    "context_codes[]": [f"course_{course_id}" for course_id in batch],
                    "start_date": start_date,
                    "active_only": "true",
                },
            )
            for item in rows:
                if item.get("id") is not None:
                    discovered[int(item["id"])] = item

        # UTS tutor announcements are often exposed only as announcement-type
        # Planner items. Resolve each known topic through the supported single
        # discussion endpoint instead of relying on the incomplete list API.
        async with self.sessions() as session:
            planner_rows = (
                await session.scalars(
                    select(PlannerItem).where(
                        PlannerItem.user_id == self.user_id,
                        PlannerItem.item_type == "announcement",
                        PlannerItem.due_at >= start_at,
                    )
                )
            ).all()
        for planner in planner_rows:
            topic_id = (
                planner.raw.get("plannable_id")
                or (planner.raw.get("plannable") or {}).get("id")
            )
            if not planner.course_id or not topic_id:
                continue
            canvas_course_id = next(
                (
                    canvas_id
                    for canvas_id, internal_id in course_by_canvas_id.items()
                    if internal_id == planner.course_id
                ),
                None,
            )
            if canvas_course_id is None:
                continue
            try:
                detail = await self.canvas.get_json(
                    f"/api/v1/courses/{canvas_course_id}/discussion_topics/{topic_id}"
                )
            except (CanvasPermissionError, httpx.HTTPStatusError):
                detail = {}
            fallback = planner.raw.get("plannable") or {}
            merged = {
                **fallback,
                **detail,
                "id": topic_id,
                "course_id": canvas_course_id,
                "context_code": f"course_{canvas_course_id}",
                "title": detail.get("title") or fallback.get("title") or planner.title,
                "posted_at": (
                    detail.get("posted_at")
                    or fallback.get("created_at")
                    or planner.raw.get("plannable_date")
                ),
                "html_url": detail.get("html_url") or planner.raw.get("html_url"),
                "read_state": detail.get("read_state") or fallback.get("read_state"),
            }
            discovered[int(topic_id)] = {**discovered.get(int(topic_id), {}), **merged}

        async with self.sessions() as session:
            for item in discovered.values():
                context_code = str(item.get("context_code") or "")
                try:
                    course_id = int(context_code.removeprefix("course_"))
                except ValueError:
                    canvas_course_id = item.get("course_id")
                else:
                    canvas_course_id = course_id
                internal_course_id = course_by_canvas_id.get(int(canvas_course_id))
                if internal_course_id is None:
                    continue
                canvas_id = int(item["id"])
                announcement = await session.scalar(
                    select(Announcement).where(
                        Announcement.user_id == self.user_id,
                        Announcement.canvas_id == canvas_id,
                    )
                )
                if announcement is None:
                    announcement = Announcement(
                        user_id=self.user_id,
                        canvas_id=canvas_id,
                        course_id=internal_course_id,
                        title=item.get("title") or "Untitled announcement",
                    )
                    session.add(announcement)
                    changed += 1
                elif announcement.raw != item:
                    changed += 1
                author = item.get("author") or {}
                announcement.course_id = internal_course_id
                announcement.title = item.get("title") or announcement.title
                announcement.message_html = sanitize_html(item.get("message") or "")
                announcement.posted_at = parse_datetime(
                    item.get("posted_at") or item.get("created_at")
                )
                announcement.author_name = (
                    author.get("display_name")
                    or author.get("name")
                    or item.get("user_name")
                )
                announcement.html_url = item.get("html_url")
                announcement.read_state = item.get("read_state")
                announcement.raw = item
                announcement.synced_at = datetime.now(timezone.utc)
                await self._upsert_document(
                    session,
                    course_id=internal_course_id,
                    title=announcement.title,
                    source_url=(
                        announcement.html_url
                        or f"canvas-announcement://{announcement.canvas_id}"
                    ),
                    content=announcement.message_html or "",
                    source_type="announcement",
                    source_id=str(announcement.canvas_id),
                    kind="announcement",
                    source_date=announcement.posted_at,
                )
            await session.commit()
        return {"count": len(discovered), "changed": changed}
