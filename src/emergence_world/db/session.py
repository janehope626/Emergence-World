"""Async database engine and transaction management."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from emergence_world.db.base import Base


def sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.resolve()}"


def sync_sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve()}"


def create_database_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    engine = create_async_engine(database_url, echo=echo, pool_pre_ping=True)
    if database_url.startswith("sqlite"):
        event.listen(engine.sync_engine, "connect", _configure_sqlite)
    return engine


def create_sync_database_engine(database_url: str, *, echo: bool = False) -> Engine:
    engine = create_engine(database_url, echo=echo, pool_pre_ping=True)
    if database_url.startswith("sqlite"):
        event.listen(engine, "connect", _configure_sqlite)
    return engine


def _configure_sqlite(dbapi_connection: Any, connection_record: Any) -> None:
    del connection_record
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA busy_timeout = 5000")
    finally:
        cursor.close()


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def create_sync_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def transaction(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Commit all work together or roll it back on any exception."""

    async with session_factory() as session, session.begin():
        yield session


@contextmanager
def sync_transaction(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Synchronous transaction boundary used by the deterministic kernel."""

    with session_factory() as session, session.begin():
        yield session


async def create_schema_for_tests(engine: AsyncEngine) -> None:
    """Create schema for isolated tests; production setup must use Alembic."""

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def drop_schema_for_tests(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


async def dispose_engine(engine: Engine | AsyncEngine) -> None:
    if isinstance(engine, AsyncEngine):
        await engine.dispose()
    else:
        engine.dispose()
