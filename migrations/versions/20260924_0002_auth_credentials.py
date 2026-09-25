"""Add magic-link sessions and encrypted credentials.

Revision ID: 20260924_0002
Revises: 20260924_0001
Create Date: 2026-09-24
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260924_0002"
down_revision: str | None = "20260924_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if "magic_link_tokens" not in existing:
        op.create_table(
            "magic_link_tokens",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("email", sa.String(320), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_magic_link_tokens_email", "magic_link_tokens", ["email"])
        op.create_index(
            "ix_magic_link_tokens_token_hash",
            "magic_link_tokens",
            ["token_hash"],
            unique=True,
        )
        op.create_index(
            "ix_magic_link_tokens_expires_at", "magic_link_tokens", ["expires_at"]
        )
    if "user_sessions" not in existing:
        op.create_table(
            "user_sessions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("csrf_hash", sa.String(64), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        )
        op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
        op.create_index(
            "ix_user_sessions_token_hash",
            "user_sessions",
            ["token_hash"],
            unique=True,
        )
        op.create_index(
            "ix_user_sessions_expires_at", "user_sessions", ["expires_at"]
        )
    if "encrypted_credentials" not in existing:
        op.create_table(
            "encrypted_credentials",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
            sa.Column("nonce", sa.LargeBinary(), nullable=False),
            sa.Column("key_version", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.UniqueConstraint(
                "user_id", "name", name="uq_encrypted_credential_owner_name"
            ),
        )
        op.create_index(
            "ix_encrypted_credentials_user_id",
            "encrypted_credentials",
            ["user_id"],
        )


def downgrade() -> None:
    op.drop_table("encrypted_credentials")
    op.drop_table("user_sessions")
    op.drop_table("magic_link_tokens")
