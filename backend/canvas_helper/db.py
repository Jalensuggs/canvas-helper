import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from starlette.requests import Request

if TYPE_CHECKING:
    from .config import Settings


class Base(DeclarativeBase):
    pass


def sync_database_url(database_url: str) -> str:
    """Rewrite an async URL to the synchronous driver Alembic should use.

    The driver is named explicitly rather than stripped: a bare ``postgresql://``
    URL resolves to whichever DBAPI SQLAlchemy defaults to, which changed from
    psycopg2 to psycopg between 2.0 and 2.1. Only psycopg (v3) is a dependency,
    so leaving the choice to SQLAlchemy breaks migrations on some versions.
    """
    if "+asyncpg" in database_url:
        return database_url.replace("+asyncpg", "+psycopg")
    if "+aiosqlite" in database_url:
        return database_url.replace("+aiosqlite", "")
    return database_url


def make_engine(database_url: str, settings: "Settings | None" = None) -> AsyncEngine:
    options: dict[str, Any] = {}
    # SQLite has no server to lose, and pooling options do not apply to it.
    if not database_url.startswith("sqlite"):
        options["pool_pre_ping"] = settings.db_pool_pre_ping if settings else True
        options["pool_recycle"] = (
            settings.db_pool_recycle_seconds if settings else 1800
        )
    return create_async_engine(database_url, **options)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    from . import models
    from .search import ensure_search_schema

    async with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        await connection.run_sync(Base.metadata.create_all)
    await ensure_search_schema(engine)
    async with make_session_factory(engine)() as session:
        if await session.get(models.User, models.LOCAL_USER_ID) is None:
            session.add(
                models.User(
                    id=models.LOCAL_USER_ID,
                    display_name="Local User",
                )
            )
            await session.commit()


async def migrate_database(database_url: str) -> None:
    """Upgrade a persistent database to the current Alembic revision."""
    if database_url.endswith("/:memory:") or database_url.endswith(":///:memory:"):
        # Alembic opens a separate connection, which is a separate SQLite memory DB.
        return
    root = (
        Path(getattr(sys, "_MEIPASS"))
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[2]
    )
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", sync_database_url(database_url))
    await asyncio.to_thread(command.upgrade, config, "head")


async def session_dependency(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session
