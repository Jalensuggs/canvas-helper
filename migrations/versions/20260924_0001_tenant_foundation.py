"""Add users, Canvas accounts, and tenant ownership.

Revision ID: 20260924_0001
Revises:
Create Date: 2026-09-24
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from canvas_helper.db import Base
from canvas_helper.models import LOCAL_USER_ID

revision: str = "20260924_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TENANT_TABLES = (
    "courses",
    "assignments",
    "planner_items",
    "local_todos",
    "documents",
    "announcements",
    "action_previews",
    "sync_runs",
)
CANVAS_ID_TABLES = ("courses", "assignments", "announcements")


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if not existing.intersection(TENANT_TABLES):
        Base.metadata.create_all(bind=bind)
        _ensure_local_user(bind)
        return

    # This is an installation from before Alembic. Preserve every row and
    # assign it to the stable local desktop owner before enforcing NOT NULL.
    Base.metadata.tables["users"].create(bind=bind, checkfirst=True)
    _ensure_local_user(bind)
    Base.metadata.tables["canvas_accounts"].create(bind=bind, checkfirst=True)

    for table_name in TENANT_TABLES:
        if table_name not in existing:
            Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)
            continue
        columns = {column["name"] for column in sa.inspect(bind).get_columns(table_name)}
        if "user_id" not in columns:
            with op.batch_alter_table(table_name) as batch:
                batch.add_column(sa.Column("user_id", sa.String(36), nullable=True))
            op.execute(
                sa.text(f"UPDATE {table_name} SET user_id = :user_id").bindparams(
                    user_id=LOCAL_USER_ID
                )
            )
            with op.batch_alter_table(table_name) as batch:
                batch.alter_column("user_id", existing_type=sa.String(36), nullable=False)
                batch.create_foreign_key(
                    f"fk_{table_name}_user_id_users", "users", ["user_id"], ["id"]
                )
                batch.create_index(f"ix_{table_name}_user_id", ["user_id"])

    for table_name in CANVAS_ID_TABLES:
        columns = {
            column["name"] for column in sa.inspect(bind).get_columns(table_name)
        }
        if "canvas_id" not in columns:
            with op.batch_alter_table(table_name) as batch:
                batch.add_column(sa.Column("canvas_id", sa.Integer(), nullable=True))
            op.execute(sa.text(f"UPDATE {table_name} SET canvas_id = id"))
            with op.batch_alter_table(table_name) as batch:
                batch.alter_column(
                    "canvas_id", existing_type=sa.Integer(), nullable=False
                )
                batch.create_unique_constraint(
                    f"uq_{table_name}_owner_canvas", ["user_id", "canvas_id"]
                )

    # The legacy planner Canvas ID was globally unique. Replace that boundary
    # with owner + Canvas ID so two users can sync the same Canvas identifier.
    inspector = sa.inspect(bind)
    planner_uniques = inspector.get_unique_constraints("planner_items")
    old_unique = next(
        (
            item
            for item in planner_uniques
            if item.get("column_names") == ["canvas_id"]
        ),
        None,
    )
    naming = {"uq": "uq_%(table_name)s_%(column_0_name)s"}
    with op.batch_alter_table(
        "planner_items", naming_convention=naming
    ) as batch:
        if old_unique is not None:
            batch.drop_constraint(
                old_unique.get("name") or "uq_planner_items_canvas_id",
                type_="unique",
            )
        if not any(
            item.get("column_names") == ["user_id", "canvas_id"]
            for item in planner_uniques
        ):
            batch.create_unique_constraint(
                "uq_planner_owner_canvas", ["user_id", "canvas_id"]
            )


def _ensure_local_user(bind) -> None:
    users = Base.metadata.tables["users"]
    present = bind.execute(
        sa.select(users.c.id).where(users.c.id == LOCAL_USER_ID)
    ).scalar_one_or_none()
    if present is None:
        bind.execute(
            users.insert().values(
                id=LOCAL_USER_ID,
                email=None,
                display_name="Local User",
            )
        )


def downgrade() -> None:
    raise RuntimeError(
        "Tenant migration downgrade is intentionally disabled to prevent data loss"
    )
