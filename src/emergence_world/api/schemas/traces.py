# 定义追踪查询接口使用的分页与资源响应模型。
"""Typed REST representations for execution tracing resources."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class TraceSummary(BaseModel):
    id: str
    world_id: str
    turn_id: str | None
    name: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    span_count: int
    state_diff_count: int
    error: str | None


class TraceSummaryPage(BaseModel):
    offset: int
    limit: int
    items: list[TraceSummary]


class CommandTrace(BaseModel):
    id: str
    world_id: str
    turn_id: str | None
    name: str
    arguments: dict[str, Any]
    status: str
    started_at: datetime
    completed_at: datetime | None
    error: str | None


class SpanTrace(BaseModel):
    id: str
    parent_span_id: str | None
    turn_id: str | None
    sequence_number: int
    stage: str
    function_name: str
    source_file: str | None
    source_line: int | None
    status: str
    started_at: datetime
    completed_at: datetime | None
    duration_ms: float | None
    error: str | None
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None


class ProviderInteractionTrace(BaseModel):
    sequence_number: int
    provider: str
    model: str
    tool_calls: list[dict[str, Any]]
    latency_ms: float | None
    cost_usd: float | None
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None


class ToolCallTrace(BaseModel):
    sequence_number: int
    tool_name: str
    arguments: dict[str, Any]
    status: str
    result: dict[str, Any] | None
    error: str | None


class WorldEventTrace(BaseModel):
    sequence_number: int
    event_type: str
    payload: dict[str, Any]


class StateDiffTrace(BaseModel):
    sequence_number: int
    entity_type: str
    entity_id: str
    path: str
    before: Any | None
    after: Any | None


class ResourcePage(BaseModel, Generic[T]):
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
    items: list[T]
