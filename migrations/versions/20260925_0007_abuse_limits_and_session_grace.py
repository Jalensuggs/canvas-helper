"""Add magic-link rate-limit bookkeeping and session rotation grace.

Revision ID: 20260925_0007
Revises: 20260924_0006
Create Date: 2026-09-25
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260925_0007"
down_revision: str | None = "20260924_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    magic_link_columns = _columns("magic_link_tokens")
    if "requester_hash" not in magic_link_columns:
        op.add_column(
            "magic_link_tokens",
            sa.Column("requester_hash", sa.String(64), nullable=True),
        )
    magic_link_indexes = _indexes("magic_link_tokens")
    if "ix_magic_link_tokens_requester_hash" not in magic_link_indexes:
        op.create_index(
            "ix_magic_link_tokens_requester_hash",
            "magic_link_tokens",
            ["requester_hash"],
        )
    if "ix_magic_link_tokens_created_at" not in magic_link_indexes:
        op.create_index(
            "ix_magic_link_tokens_created_at", "magic_link_tokens", ["created_at"]
        )

    session_columns = _columns("user_sessions")
    for name in ("previous_token_hash", "previous_csrf_hash"):
        if name not in session_columns:
            op.add_column("user_sessions", sa.Column(name, sa.String(64), nullable=True))
    if "previous_expires_at" not in session_columns:
        op.add_column(
            "user_sessions",
            sa.Column("previous_expires_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "ix_user_sessions_previous_token_hash" not in _indexes("user_sessions"):
        op.create_index(
            "ix_user_sessions_previous_token_hash",
            "user_sessions",
            ["previous_token_hash"],
        )


def downgrade() -> None:
    for name in (
        "ix_user_sessions_previous_token_hash",
        "ix_magic_link_tokens_requester_hash",
        "ix_magic_link_tokens_created_at",
    ):
        op.drop_index(name)
    op.drop_column("user_sessions", "previous_expires_at")
    op.drop_column("user_sessions", "previous_csrf_hash")
    op.drop_column("user_sessions", "previous_token_hash")
    op.drop_column("magic_link_tokens", "requester_hash")
