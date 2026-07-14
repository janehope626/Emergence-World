# 提供命令追踪、跨度、模型交互、工具调用、事件和状态差异的查询端点。
"""Read-only execution trace REST resources."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from emergence_world.api.dependencies import (
    authorize_payload_access,
    database_session,
)
from emergence_world.api.schemas.traces import (
    CommandTrace,
    ProviderInteractionTrace,
    ResourcePage,
    SpanTrace,
    StateDiffTrace,
    ToolCallTrace,
    TraceSummaryPage,
    WorldEventTrace,
)
from emergence_world.db.models import (
    CommandExecution,
    ExecutionSpan,
    ProviderInteraction,
    StateDiff,
    ToolCall,
    WorldEvent,
)
from emergence_world.observability.query import list_trace_summaries

router = APIRouter(prefix="/traces", tags=["traces"])
DatabaseSession = Annotated[Session, Depends(database_session)]


def _command_or_404(session: Session, command_id: str) -> CommandExecution:
    command = session.get(CommandExecution, command_id)
    if command is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="trace command not found"
        )
    return command


def _turn_id(session: Session, command_id: str) -> str | None:
    return session.scalar(
        select(ExecutionSpan.turn_id)
        .where(
            ExecutionSpan.command_id == command_id,
            ExecutionSpan.turn_id.is_not(None),
        )
        .order_by(ExecutionSpan.sequence_number)
        .limit(1)
    )


def _page(offset: int, limit: int, items: list[Any]) -> dict[str, Any]:
    return {"offset": offset, "limit": limit, "items": items}


@router.get("", response_model=TraceSummaryPage)
def list_traces(
    session: DatabaseSession,
    world_id: str | None = None,
    stage: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    started_from: datetime | None = Query(None, alias="from"),
    started_to: datetime | None = Query(None, alias="to"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    items = list_trace_summaries(
        session,
        world_id=world_id,
        stage=stage,
        status=status_filter,
        started_from=started_from,
        started_to=started_to,
        offset=offset,
        limit=limit,
    )
    return _page(offset, limit, items)


@router.get("/{command_id}", response_model=CommandTrace)
def get_trace(command_id: str, session: DatabaseSession) -> dict[str, Any]:
    command = _command_or_404(session, command_id)
    return {
        "id": command.id,
        "world_id": command.world_id,
        "turn_id": _turn_id(session, command.id),
        "name": command.command_name,
        "arguments": command.arguments_json,
        "status": command.status,
        "started_at": command.started_at,
        "completed_at": command.completed_at,
        "error": command.error,
    }


@router.get("/{command_id}/spans", response_model=ResourcePage[SpanTrace])
def get_spans(
    command_id: str,
    request: Request,
    session: DatabaseSession,
    stage: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    include_payloads: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    _command_or_404(session, command_id)
    authorize_payload_access(request, include_payloads)
    query = select(ExecutionSpan).where(ExecutionSpan.command_id == command_id)
    if stage is not None:
        query = query.where(ExecutionSpan.stage == stage)
    if status_filter is not None:
        query = query.where(ExecutionSpan.status == status_filter)
    records = session.scalars(
        query.order_by(ExecutionSpan.sequence_number).offset(offset).limit(limit)
    ).all()
    items = [
        {
            "id": item.id,
            "parent_span_id": item.parent_span_id,
            "turn_id": item.turn_id,
            "sequence_number": item.sequence_number,
            "stage": item.stage,
            "function_name": item.function_name,
            "source_file": item.source_file,
            "source_line": item.source_line,
            "status": item.status,
            "started_at": item.started_at,
            "completed_at": item.completed_at,
            "duration_ms": item.duration_ms,
            "error": item.error,
            **(
                {"input": item.input_json, "output": item.output_json}
                if include_payloads
                else {}
            ),
        }
        for item in records
    ]
    return _page(offset, limit, items)


@router.get(
    "/{command_id}/provider-interactions",
    response_model=ResourcePage[ProviderInteractionTrace],
)
def get_provider_interactions(
    command_id: str,
    request: Request,
    session: DatabaseSession,
    include_payloads: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    _command_or_404(session, command_id)
    authorize_payload_access(request, include_payloads)
    turn_id = _turn_id(session, command_id)
    records = [] if turn_id is None else session.scalars(
        select(ProviderInteraction)
        .where(ProviderInteraction.turn_id == turn_id)
        .order_by(ProviderInteraction.sequence_number)
        .offset(offset)
        .limit(limit)
    ).all()
    items = [
        {
            "sequence_number": item.sequence_number,
            "provider": item.provider,
            "model": item.model_name,
            "tool_calls": item.parsed_tool_calls_json,
            "latency_ms": item.latency_ms,
            "cost_usd": item.cost_usd,
            **(
                {
                    "request": item.request_json,
                    "response": item.raw_response_json,
                }
                if include_payloads
                else {}
            ),
        }
        for item in records
    ]
    return _page(offset, limit, items)


@router.get("/{command_id}/tool-calls", response_model=ResourcePage[ToolCallTrace])
def get_tool_calls(
    command_id: str,
    session: DatabaseSession,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    _command_or_404(session, command_id)
    turn_id = _turn_id(session, command_id)
    records = [] if turn_id is None else session.scalars(
        select(ToolCall)
        .where(ToolCall.turn_id == turn_id)
        .order_by(ToolCall.sequence_number)
        .offset(offset)
        .limit(limit)
    ).all()
    return _page(
        offset,
        limit,
        [
            {
                "sequence_number": item.sequence_number,
                "tool_name": item.tool_name,
                "arguments": item.arguments_json,
                "status": item.status.value,
                "result": item.result_json,
                "error": item.error,
            }
            for item in records
        ],
    )


@router.get("/{command_id}/events", response_model=ResourcePage[WorldEventTrace])
def get_events(
    command_id: str,
    session: DatabaseSession,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    _command_or_404(session, command_id)
    turn_id = _turn_id(session, command_id)
    records = [] if turn_id is None else session.scalars(
        select(WorldEvent)
        .where(WorldEvent.turn_id == turn_id)
        .order_by(WorldEvent.sequence_number)
        .offset(offset)
        .limit(limit)
    ).all()
    return _page(
        offset,
        limit,
        [
            {
                "sequence_number": item.sequence_number,
                "event_type": item.event_type,
                "payload": item.payload_json,
            }
            for item in records
        ],
    )


@router.get("/{command_id}/state-diffs", response_model=ResourcePage[StateDiffTrace])
def get_state_diffs(
    command_id: str,
    session: DatabaseSession,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    _command_or_404(session, command_id)
    records = session.scalars(
        select(StateDiff)
        .where(StateDiff.command_id == command_id)
        .order_by(StateDiff.sequence_number)
        .offset(offset)
        .limit(limit)
    ).all()
    return _page(
        offset,
        limit,
        [
            {
                "sequence_number": item.sequence_number,
                "entity_type": item.entity_type,
                "entity_id": item.entity_id,
                "path": item.path,
                "before": item.before_json,
                "after": item.after_json,
            }
            for item in records
        ],
    )
