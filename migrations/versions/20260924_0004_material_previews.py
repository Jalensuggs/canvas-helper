"""Add downloaded material metadata and preview fields.

Revision ID: 20260924_0004
Revises: 20260924_0003
Create Date: 2026-09-24
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260924_0004"
down_revision: str | None = "20260924_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("documents")}
    indexes = {index["name"] for index in inspector.get_indexes("documents")}
    constraints = {
        constraint["name"] for constraint in inspector.get_unique_constraints("documents")
    }
    additions = (
        ("mime_type", sa.String(255), True),
        ("kind", sa.String(50), False),
        ("size", sa.Integer(), True),
        ("version", sa.Integer(), False),
        ("source_type", sa.String(50), True),
        ("source_id", sa.String(200), True),
        ("canvas_file_id", sa.String(100), True),
        ("metadata_json", sa.JSON(), False),
        ("preview_html", sa.Text(), True),
        ("source_updated_at", sa.DateTime(timezone=True), True),
        ("downloaded_at", sa.DateTime(timezone=True), True),
        ("extracted_at", sa.DateTime(timezone=True), True),
    )
    with op.batch_alter_table("documents") as batch:
        for name, type_, nullable in additions:
            if name not in columns:
                kwargs = {"nullable": nullable}
                if name == "kind":
                    kwargs["server_default"] = "page"
                elif name == "version":
                    kwargs["server_default"] = "1"
                elif name == "metadata_json":
                    kwargs["server_default"] = "{}"
                batch.add_column(sa.Column(name, type_, **kwargs))
        if "ix_documents_source_type" not in indexes:
            batch.create_index("ix_documents_source_type", ["source_type"])
        if "ix_documents_source_id" not in indexes:
            batch.create_index("ix_documents_source_id", ["source_id"])
        if "ix_documents_canvas_file_id" not in indexes:
            batch.create_index("ix_documents_canvas_file_id", ["canvas_file_id"])
        if "uq_documents_owner_source" not in constraints:
            batch.create_unique_constraint(
                "uq_documents_owner_source", ["user_id", "source_type", "source_id"]
            )


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint("uq_documents_owner_source", type_="unique")
        batch.drop_index("ix_documents_canvas_file_id")
        batch.drop_index("ix_documents_source_id")
        batch.drop_index("ix_documents_source_type")
        for name in (
            "extracted_at", "downloaded_at", "source_updated_at", "preview_html",
            "metadata_json", "canvas_file_id", "source_id", "source_type",
            "version", "size", "kind", "mime_type",
        ):
            batch.drop_column(name)
