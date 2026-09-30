"""What the student's Canvas account currently says, as facts for the AI.

Retrieval answers "what does my course material say about X". It cannot answer
"what do I have due", because a deadline is a row with a date, not a passage of
text to search for. This module reads those rows and writes them out for the
model alongside the retrieved passages.
"""

from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import Assignment, Course, LocalTodo, PlannerItem

MAX_COURSES = 30
MAX_DEADLINES = 25
MAX_TODOS = 20


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


def _local(value: datetime | None, settings: Settings) -> str:
    if value is None:
        return "未设截止时间"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(settings.tzinfo).strftime("%Y-%m-%d %H:%M")


async def student_snapshot(
    session: AsyncSession,
    user_id: str,
    settings: Settings,
    *,
    course_id: int | None = None,
    now: datetime | None = None,
) -> str:
    """Render the student's courses, open deadlines and to-dos as plain text.

    ``course_id`` is the internal id of the course the chat is scoped to; when
    given, deadlines are limited to it, matching what the context panel says is
    being sent.
    """
    now = now or datetime.now(timezone.utc)
    term_course_ids = await current_course_ids(session, user_id, settings)
    if course_id is not None:
        term_course_ids = {course_id} & term_course_ids or {course_id}

    courses = (
        await session.scalars(
            select(Course)
            .where(Course.user_id == user_id, Course.id.in_(term_course_ids))
            .order_by(Course.name)
        )
    ).all()
    names = {row.id: (row.name or row.course_code or f"课程 {row.canvas_id}") for row in courses}

    assignments = [
        row
        for row in (
            await session.scalars(
                select(Assignment)
                .where(
                    Assignment.user_id == user_id,
                    Assignment.course_id.in_(term_course_ids),
                    Assignment.due_at.is_not(None),
                    Assignment.due_at >= settings.current_term_start(now),
                )
                .order_by(Assignment.due_at)
            )
        ).all()
        if not assignment_completed(row)
    ]
    todos = [
        row
        for row in (
            await session.scalars(
                select(LocalTodo)
                .where(LocalTodo.user_id == user_id)
                .order_by(LocalTodo.due_at)
            )
        ).all()
        if not row.completed
    ]

    lines = [
        f"今天是 {now.astimezone(settings.tzinfo).strftime('%Y-%m-%d %H:%M')}"
        f"（{settings.academic_timezone}）。以下是这名学生 Canvas 账号里已同步的数据。"
    ]
    if not courses:
        lines.append(
            "课程：没有。该账号还没有同步到课程，或本学期没有课程；"
            "不要猜测学生在上什么课。"
        )
    else:
        head = "当前筛选的课程" if course_id is not None else f"本学期课程（{len(courses)}）"
        lines.append(f"{head}：")
        lines.extend(f"- {names[row.id]}" for row in courses[:MAX_COURSES])

    if not assignments:
        lines.append("未完成的作业：没有。所有已同步的作业都已提交、已评分或已过期。")
    else:
        lines.append(f"未完成的作业与测验，按截止时间排（{len(assignments)}）：")
        for row in assignments[:MAX_DEADLINES]:
            points = f" · {row.points_possible:g} 分" if row.points_possible else ""
            lines.append(
                f"- {_local(row.due_at, settings)} · "
                f"{names.get(row.course_id, '未知课程')} · {row.name}{points}"
            )
        if len(assignments) > MAX_DEADLINES:
            lines.append(f"- （还有 {len(assignments) - MAX_DEADLINES} 项未列出）")

    if todos:
        lines.append(f"学生自建的待办（{len(todos)}）：")
        for row in todos[:MAX_TODOS]:
            lines.append(f"- {_local(row.due_at, settings)} · {row.title}")

    return "\n".join(lines)
