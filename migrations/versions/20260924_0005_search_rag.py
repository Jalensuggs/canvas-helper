"""Add tenant-scoped chunks and full-text indexes.

Revision ID: 20260924_0005
Revises: 20260924_0004
Create Date: 2026-09-24
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260924_0005"
down_revision: str | None = "20260924_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    if "doc_chunks" not in existing:
        op.create_table(
            "doc_chunks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "document_id", sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.Column("source_kind", sa.String(50), nullable=False),
        sa.Column("source_id", sa.String(300), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("slide", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("source_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id", "source_kind", "source_id", "ordinal",
            name="uq_doc_chunks_owner_source_ordinal",
        ),
        )
        op.create_index("ix_doc_chunks_user_id", "doc_chunks", ["user_id"])
        op.create_index("ix_doc_chunks_document_id", "doc_chunks", ["document_id"])
        op.create_index("ix_doc_chunks_course_id", "doc_chunks", ["course_id"])
        op.create_index("ix_doc_chunks_source_kind", "doc_chunks", ["source_kind"])
        op.create_index("ix_doc_chunks_source_id", "doc_chunks", ["source_id"])
        op.create_index("ix_doc_chunks_source_date", "doc_chunks", ["source_date"])
        op.create_index(
            "ix_doc_chunks_owner_course_kind", "doc_chunks",
            ["user_id", "course_id", "source_kind"],
        )
        op.create_index(
            "ix_doc_chunks_owner_date", "doc_chunks", ["user_id", "source_date"]
        )

    if bind.dialect.name == "sqlite":
        # A contentless FTS table plus triggers gives actual FTS5 lifecycle
        # semantics while doc_chunks remains the source of truth.
        op.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5("
            "chunk_id UNINDEXED, user_id UNINDEXED, title, content, "
            "tokenize='trigram')"
        )
        op.execute(
            "CREATE TRIGGER IF NOT EXISTS doc_chunks_fts_insert AFTER INSERT ON doc_chunks BEGIN "
            "INSERT INTO search_index(rowid, chunk_id, user_id, title, content) "
            "VALUES (new.id, new.id, new.user_id, new.title, new.content); END"
        )
        op.execute(
            "CREATE TRIGGER IF NOT EXISTS doc_chunks_fts_delete AFTER DELETE ON doc_chunks BEGIN "
            "DELETE FROM search_index WHERE rowid = old.id; END"
        )
        op.execute(
            "CREATE TRIGGER IF NOT EXISTS doc_chunks_fts_update AFTER UPDATE ON doc_chunks BEGIN "
            "DELETE FROM search_index WHERE rowid = old.id; "
            "INSERT INTO search_index(rowid, chunk_id, user_id, title, content) "
            "VALUES (new.id, new.id, new.user_id, new.title, new.content); END"
        )
    elif bind.dialect.name == "postgresql":
        columns = {column["name"] for column in inspector.get_columns("doc_chunks")}
        if "search_vector" not in columns:
            op.execute(
                "ALTER TABLE doc_chunks ADD COLUMN search_vector tsvector "
                "GENERATED ALWAYS AS ("
                "to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(content, ''))"
                ") STORED"
            )
        op.execute(
            "CREATE INDEX ix_doc_chunks_search_vector ON doc_chunks "
            "USING GIN (search_vector)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS doc_chunks_fts_update")
        op.execute("DROP TRIGGER IF EXISTS doc_chunks_fts_delete")
        op.execute("DROP TRIGGER IF EXISTS doc_chunks_fts_insert")
        op.execute("DROP TABLE IF EXISTS search_index")
    elif bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_doc_chunks_search_vector")
    op.drop_table("doc_chunks")
