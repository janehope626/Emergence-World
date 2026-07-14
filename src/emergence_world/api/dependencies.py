# 提供 API 路由共用的数据库会话与敏感载荷访问授权依赖。
"""FastAPI request dependencies."""

from __future__ import annotations

from collections.abc import Iterator
from hmac import compare_digest

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session, sessionmaker


def database_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session


def authorize_payload_access(request: Request, include_payloads: bool) -> None:
    if not include_payloads:
        return
    configured: str | None = request.app.state.payload_access_token
    supplied = request.headers.get("X-Trace-Payload-Token")
    if configured is None or supplied is None or not compare_digest(configured, supplied):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="full trace payload access is disabled or unauthorized",
        )
