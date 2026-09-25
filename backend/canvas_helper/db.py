import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
import sys

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


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url)


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
    config.set_main_option(
        "sqlalchemy.url",
        database_url.replace("+aiosqlite", "").replace("+asyncpg", ""),
    )
    await asyncio.to_thread(command.upgrade, config, "head")


async def session_dependency(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session
