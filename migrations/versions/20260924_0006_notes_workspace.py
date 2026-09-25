"""Add tenant-scoped Markdown notes and revisions.

Revision ID: 20260924_0006
Revises: 20260924_0005
Create Date: 2026-09-24
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260924_0006"
down_revision: str | None = "20260924_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "notes" not in existing:
        op.create_table(
        "notes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id"), nullable=True),
        sa.Column("assignment_id", sa.Integer(), sa.ForeignKey("assignments.id"), nullable=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=True),
        sa.Column("chunk_id", sa.Integer(), sa.ForeignKey("doc_chunks.id"), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("slide", sa.Integer(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        for column in ("user_id", "course_id", "assignment_id", "document_id", "chunk_id", "deleted_at"):
            op.create_index(f"ix_notes_{column}", "notes", [column])
        op.create_index("ix_notes_owner_updated", "notes", ["user_id", "updated_at"])
        op.create_index(
            "ix_notes_owner_target", "notes",
            ["user_id", "course_id", "assignment_id", "document_id", "chunk_id"],
        )
    if "note_revisions" not in existing:
        op.create_table(
        "note_revisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("note_id", sa.Integer(), sa.ForeignKey("notes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False, server_default=""),
        sa.Column("reason", sa.String(30), nullable=False, server_default="save"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("note_id", "version", name="uq_note_revisions_note_version"),
        )
        op.create_index("ix_note_revisions_user_id", "note_revisions", ["user_id"])
        op.create_index("ix_note_revisions_note_id", "note_revisions", ["note_id"])
        op.create_index(
            "ix_note_revisions_owner_note", "note_revisions", ["user_id", "note_id"]
        )


def downgrade() -> None:
    op.drop_table("note_revisions")
    op.drop_table("notes")
