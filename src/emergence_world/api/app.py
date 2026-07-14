# 创建追踪查询服务，并在应用生命周期中管理数据库迁移与实时事件代理。
"""FastAPI application factory for the observability service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from os import environ
from pathlib import Path
from typing import AsyncIterator

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from emergence_world.api.realtime.websocket import router as websocket_router
from emergence_world.api.routes.health import router as health_router
from emergence_world.api.routes.traces import router as traces_router
from emergence_world.db.session import (
    create_sync_database_engine,
    create_sync_session_factory,
    sync_sqlite_url,
)


def _migrate(database: Path) -> None:
    repository_root = Path(__file__).resolve().parents[3]
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", sync_sqlite_url(database))
    command.upgrade(config, "head")


def create_app(
    database: Path = Path("emergence_world.db"),
    *,
    payload_access_token: str | None = None,
    cors_origins: tuple[str, ...] = (),
    migrate: bool = True,
) -> FastAPI:
    """Create one single-process observability API application."""

    if migrate:
        _migrate(database)
    engine = create_sync_database_engine(sync_sqlite_url(database))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        yield
        engine.dispose()

    app = FastAPI(
        title="Emergence World Observability API",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.state.session_factory = create_sync_session_factory(engine)
    app.state.payload_access_token = payload_access_token or environ.get(
        "EMERGENCE_TRACE_PAYLOAD_TOKEN"
    )
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_origins),
            allow_credentials=False,
            allow_methods=["GET"],
            allow_headers=["X-Trace-Payload-Token", "Content-Type"],
        )
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(traces_router, prefix="/api/v1")
    app.include_router(websocket_router)
    return app
