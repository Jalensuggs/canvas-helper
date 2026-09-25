"""Add PostgreSQL trigram indexes so CJK queries are searchable.

Revision ID: 20260925_0008
Revises: 20260925_0007
Create Date: 2026-09-25

``to_tsvector('simple', ...)`` splits on whitespace, so a Chinese sentence
becomes a single token and no substring of it ever matches. SQLite already
avoids this by using an FTS5 trigram tokenizer; pg_trgm is the equivalent on
PostgreSQL. Creating an extension needs elevated rights, so a refusal is
tolerated: the query layer falls back to an unindexed scan and logs nothing
user-visible.
"""
from collections.abc import Sequence
import logging

from alembic import op
import sqlalchemy as sa

revision: str = "20260925_0008"
down_revision: str | None = "20260925_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    try:
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    except sa.exc.DatabaseError:
        logger.warning(
            "Could not create the pg_trgm extension; CJK search will fall back "
            "to an unindexed scan. Grant rights and re-run to enable the index."
        )
        return
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_doc_chunks_content_trgm ON doc_chunks "
        "USING GIN (content gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_doc_chunks_title_trgm ON doc_chunks "
        "USING GIN (title gin_trgm_ops)"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_doc_chunks_title_trgm")
    op.execute("DROP INDEX IF EXISTS ix_doc_chunks_content_trgm")
