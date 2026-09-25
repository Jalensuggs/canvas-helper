"""Add persisted sync queue and adaptive schedules.

Revision ID: 20260924_0003
Revises: 20260924_0002
Create Date: 2026-09-24
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260924_0003"
down_revision: str | None = "20260924_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if "sync_jobs" not in existing:
        op.create_table(
            "sync_jobs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("job", sa.String(80), nullable=False),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("dedupe_key", sa.String(180)),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("lease_owner", sa.String(100)),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
            sa.Column("cancel_requested", sa.Boolean(), nullable=False),
            sa.Column("result", sa.JSON()),
            sa.Column("error", sa.Text()),
            sa.Column("change_count", sa.Integer()),
            sa.Column("rate_limit_remaining", sa.Integer()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True)),
            sa.Column("finished_at", sa.DateTime(timezone=True)),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.UniqueConstraint("dedupe_key", name="uq_sync_jobs_dedupe_key"),
        )
        op.create_index("ix_sync_jobs_user_id", "sync_jobs", ["user_id"])
        op.create_index("ix_sync_jobs_job", "sync_jobs", ["job"])
        op.create_index("ix_sync_jobs_status", "sync_jobs", ["status"])
        op.create_index("ix_sync_jobs_available_at", "sync_jobs", ["available_at"])
        op.create_index("ix_sync_jobs_lease_owner", "sync_jobs", ["lease_owner"])
        op.create_index("ix_sync_jobs_lease_expires_at", "sync_jobs", ["lease_expires_at"])
        op.create_index(
            "ix_sync_jobs_claim",
            "sync_jobs",
            ["status", "available_at", "created_at"],
        )
        op.create_index(
            "ix_sync_jobs_user_created", "sync_jobs", ["user_id", "created_at"]
        )
    if "sync_schedules" not in existing:
        op.create_table(
            "sync_schedules",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("job", sa.String(80), nullable=False),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("interval_seconds", sa.Integer(), nullable=False),
            sa.Column("recent_change_count", sa.Integer(), nullable=False),
            sa.Column("rate_limit_remaining", sa.Integer()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.UniqueConstraint("user_id", "job", name="uq_sync_schedules_user_job"),
        )
        op.create_index("ix_sync_schedules_user_id", "sync_schedules", ["user_id"])
        op.create_index("ix_sync_schedules_next_run_at", "sync_schedules", ["next_run_at"])
        op.create_index("ix_sync_schedules_due", "sync_schedules", ["next_run_at"])


def downgrade() -> None:
    op.drop_table("sync_schedules")
    op.drop_table("sync_jobs")
