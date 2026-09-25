import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .canvas.client import CanvasClient
from .models import ActionPreview, PlannerItem

ACTION_RISKS = {
    "planner.complete": "low",
    "planner.read": "low",
    "planner.note.create": "medium",
    "planner.note.update": "medium",
}


class ActionError(ValueError):
    pass


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ActionService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        ttl_seconds: int,
        user_id: str,
        canvas: CanvasClient | None = None,
    ):
        self.sessions = sessions
        self.ttl_seconds = ttl_seconds
        self.user_id = user_id
        self.canvas = canvas

    async def preview(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action not in ACTION_RISKS:
            raise ActionError("Unsupported action")
        if action.startswith("planner.note."):
            title = str(payload.get("title") or "").strip()
            details = str(payload.get("details") or "")
            todo_date = payload.get("todo_date")
            course_id = payload.get("course_id")
            note_id = payload.get("planner_note_id")
            if not title or not todo_date:
                raise ActionError("title and todo_date are required")
            if action == "planner.note.update" and note_id is None:
                raise ActionError("planner_note_id is required for update")
            try:
                normalized = {
                    "title": title,
                    "details": details,
                    "todo_date": (
                        todo_date.isoformat()
                        if isinstance(todo_date, datetime)
                        else str(todo_date)
                    ),
                    "course_id": int(course_id) if course_id is not None else None,
                    "planner_note_id": int(note_id) if note_id is not None else None,
                }
            except (TypeError, ValueError) as exc:
                raise ActionError("course_id and planner_note_id must be integers") from exc
            operation = "create" if action.endswith("create") else "update"
            token = secrets.token_urlsafe(32)
            expires = datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)
            async with self.sessions() as session:
                # A supplied course must be one of this user's synced courses.
                if normalized["course_id"] is not None:
                    from .models import Course
                    course = await session.scalar(select(Course).where(
                        Course.user_id == self.user_id,
                        Course.canvas_id == normalized["course_id"],
                    ))
                    if course is None:
                        raise ActionError("Course not found")
                session.add(ActionPreview(
                    user_id=self.user_id, token_hash=_hash_token(token),
                    action=action, risk=ACTION_RISKS[action], payload=normalized,
                    expires_at=expires,
                ))
                await session.commit()
            fields = {
                "title": normalized["title"],
                "details": normalized["details"],
                "todo_date": normalized["todo_date"],
                "course_id": normalized["course_id"],
            }
            return {
                "action": action, "operation": operation,
                "risk": ACTION_RISKS[action],
                "summary": f"{operation.title()} Canvas Planner Note “{title}”",
                "remote_endpoint": (
                    "/api/v1/planner_notes"
                    if operation == "create"
                    else f"/api/v1/planner_notes/{normalized['planner_note_id']}"
                ),
                "fields": fields, "confirm_token": token, "expires_at": expires,
            }
        try:
            item_id = int(payload["planner_item_id"])
            state = bool(payload["state"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ActionError("planner_item_id and boolean state are required") from exc
        if not isinstance(payload.get("state"), bool):
            raise ActionError("state must be boolean")
        async with self.sessions() as session:
            item = await session.scalar(
                select(PlannerItem).where(
                    PlannerItem.id == item_id,
                    PlannerItem.user_id == self.user_id,
                )
            )
            if item is None:
                raise ActionError("Planner item not found")
            normalized = {"planner_item_id": item_id, "state": state}
            token = secrets.token_urlsafe(32)
            expires = datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)
            session.add(
                ActionPreview(
                    user_id=self.user_id,
                    token_hash=_hash_token(token),
                    action=action,
                    risk=ACTION_RISKS[action],
                    payload=normalized,
                    expires_at=expires,
                )
            )
            await session.commit()
            return {
                "action": action,
                "risk": ACTION_RISKS[action],
                "summary": f"{item.title}: set {action.split('.')[-1]}={state}",
                "confirm_token": token,
                "expires_at": expires,
            }

    async def execute(self, action: str, token: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        token_hash = _hash_token(token)
        async with self.sessions() as session:
            preview = await session.scalar(
                select(ActionPreview)
                .where(
                    ActionPreview.token_hash == token_hash,
                    ActionPreview.user_id == self.user_id,
                )
                .with_for_update()
            )
            if preview is None:
                raise ActionError("Invalid confirmation token")
            expires = preview.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if preview.action != action or preview.consumed_at or expires <= now:
                raise ActionError("Confirmation token expired, used, or mismatched")
            claimed = await session.execute(
                update(ActionPreview)
                .where(
                    ActionPreview.id == preview.id,
                    ActionPreview.user_id == self.user_id,
                    ActionPreview.consumed_at.is_(None),
                )
                .values(consumed_at=now)
            )
            if claimed.rowcount != 1:
                raise ActionError("Confirmation token was already used")
            payload = preview.payload
            if action.startswith("planner.note."):
                await session.commit()
                item = None
                item_raw = {}
            else:
                item = await session.scalar(
                    select(PlannerItem).where(
                        PlannerItem.id == payload["planner_item_id"],
                        PlannerItem.user_id == self.user_id,
                    )
                )
                if item is None:
                    raise ActionError("Planner item no longer exists")
                item_raw = item.raw
                await session.commit()

        if action.startswith("planner.note."):
            if not self.canvas:
                raise ActionError("Canvas connection is required")
            fields = {
                "title": payload["title"],
                "details": payload["details"],
                "todo_date": payload["todo_date"],
            }
            if payload.get("course_id") is not None:
                fields["course_id"] = payload["course_id"]
            note_id = payload.get("planner_note_id")
            if action == "planner.note.create":
                response = await self.canvas.request(
                    "POST", "/api/v1/planner_notes", json=fields
                )
                created = response.json()
                note_id = created.get("id")
                if note_id is None:
                    raise ActionError("Canvas did not return the created Planner Note id")
            else:
                await self.canvas.request(
                    "PUT", f"/api/v1/planner_notes/{note_id}", json=fields
                )
            # Verify with an independent read. Mutating requests are never retried by
            # CanvasClient, so an ambiguous write cannot be duplicated blindly.
            verified = await self.canvas.get_json(f"/api/v1/planner_notes/{note_id}")
            for key in ("title", "details", "todo_date"):
                expected = str(fields[key])
                actual = str(verified.get(key) or "")
                if key == "todo_date":
                    expected, actual = expected.replace("+00:00", "Z"), actual.replace("+00:00", "Z")
                if actual != expected:
                    raise ActionError(f"Canvas Planner Note readback mismatch for {key}")
            if fields.get("course_id") is not None and int(verified.get("course_id") or 0) != fields["course_id"]:
                raise ActionError("Canvas Planner Note readback mismatch for course_id")
            return {
                "action": action, "executed": True, "verified": True,
                "planner_note_id": int(note_id), "remote": verified,
            }

        if action == "planner.complete" and self.canvas:
            override_id = (item_raw.get("planner_override") or {}).get("id")
            if override_id:
                response = await self.canvas.request(
                    "PUT",
                    f"/api/v1/planner/overrides/{override_id}",
                    json={"marked_complete": payload["state"]},
                )
            else:
                plannable_id = item_raw.get("plannable_id") or item_raw.get("id")
                plannable_type = item_raw.get("plannable_type")
                if not plannable_id or not plannable_type:
                    raise ActionError("Planner item lacks Canvas override identifiers")
                response = await self.canvas.request(
                    "POST",
                    "/api/v1/planner/overrides",
                    json={
                        "plannable_id": plannable_id,
                        "plannable_type": plannable_type,
                        "marked_complete": payload["state"],
                    },
                )
            if bool(response.json().get("marked_complete")) != payload["state"]:
                raise ActionError("Canvas did not confirm the requested state")

        async with self.sessions() as session:
            verified = await session.scalar(
                select(PlannerItem).where(
                    PlannerItem.id == payload["planner_item_id"],
                    PlannerItem.user_id == self.user_id,
                )
            )
            if verified is None:
                raise ActionError("Planner item disappeared during execution")
            field = "completed" if action == "planner.complete" else "read"
            setattr(verified, field, payload["state"])
            await session.commit()
            await session.refresh(verified)
            return {
                "action": action,
                "executed": True,
                "verified": getattr(verified, field) == payload["state"],
                "planner_item_id": payload["planner_item_id"],
                "state": payload["state"],
            }
