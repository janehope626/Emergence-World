# 查询、分页和清理已提交的执行追踪记录。
"""Read and retention operations for persisted execution traces."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, delete, func, select
from sqlalchemy.orm import Session

from emergence_world.db.base import utc_now
from emergence_world.db.models import (
    CommandExecution,
    ExecutionSpan,
    StateDiff,
    TraceStreamEvent,
)


def list_trace_summaries(
    session: Session,
    *,
    world_id: str | None = None,
    stage: str | None = None,
    status: str | None = None,
    started_from: datetime | None = None,
    started_to: datetime | None = None,
    offset: int = 0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return bounded trace summaries without loading recorded payloads."""

    query: Select[tuple[CommandExecution]] = select(CommandExecution)
    if stage is not None:
        query = query.where(
            CommandExecution.id.in_(
                select(ExecutionSpan.command_id).where(ExecutionSpan.stage == stage)
            )
        )
    if world_id is not None:
        query = query.where(CommandExecution.world_id == world_id)
    if status is not None:
        query = query.where(CommandExecution.status == status)
    if started_from is not None:
        query = query.where(CommandExecution.started_at >= started_from)
    if started_to is not None:
        query = query.where(CommandExecution.started_at <= started_to)
    commands = session.scalars(
        query.order_by(CommandExecution.started_at.desc()).offset(offset).limit(limit)
    ).all()
    summaries: list[dict[str, Any]] = []
    for command in commands:
        span_count = session.scalar(
            select(func.count())
            .select_from(ExecutionSpan)
            .where(ExecutionSpan.command_id == command.id)
        ) or 0
        diff_count = session.scalar(
            select(func.count())
            .select_from(StateDiff)
            .where(StateDiff.command_id == command.id)
        ) or 0
        turn_id = session.scalar(
            select(ExecutionSpan.turn_id)
            .where(
                ExecutionSpan.command_id == command.id,
                ExecutionSpan.turn_id.is_not(None),
            )
            .order_by(ExecutionSpan.sequence_number)
            .limit(1)
        )
        summaries.append(
            {
                "id": command.id,
                "world_id": command.world_id,
                "turn_id": turn_id,
                "name": command.command_name,
                "status": command.status,
                "started_at": command.started_at.isoformat(),
                "completed_at": (
                    command.completed_at.isoformat()
                    if command.completed_at is not None
                    else None
                ),
                "span_count": span_count,
                "state_diff_count": diff_count,
                "error": command.error,
            }
        )
    return summaries


def trace_prune_candidates(
    session: Session,
    *,
    older_than_days: int,
    keep_latest: int,
    world_id: str | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Select old commands while always preserving the newest bounded set."""

    scope = select(CommandExecution.id)
    if world_id is not None:
        scope = scope.where(CommandExecution.world_id == world_id)
    protected = scope.order_by(CommandExecution.started_at.desc()).limit(keep_latest)
    cutoff = (now or utc_now()) - timedelta(days=older_than_days)
    query = select(CommandExecution.id).where(
        CommandExecution.started_at < cutoff,
        CommandExecution.id.not_in(protected),
    )
    if world_id is not None:
        query = query.where(CommandExecution.world_id == world_id)
    return list(session.scalars(query.order_by(CommandExecution.started_at)).all())


def delete_traces(session: Session, command_ids: list[str]) -> int:
    """Delete trace roots; database cascades remove spans and state diffs."""

    if not command_ids:
        return 0
    session.execute(delete(CommandExecution).where(CommandExecution.id.in_(command_ids)))
    return len(command_ids)


def latest_trace_stream_sequence(session: Session) -> int:
    return session.scalar(select(func.max(TraceStreamEvent.stream_sequence))) or 0


def committed_trace_stream_events(
    session: Session,
    *,
    after_sequence: int,
    world_id: str | None = None,
    command_id: str | None = None,
    limit: int = 200,
) -> tuple[int, list[dict[str, Any]]]:
    """Read committed outbox events and return a safe reconnect cursor."""

    observed_max = latest_trace_stream_sequence(session)
    if observed_max <= after_sequence:
        return after_sequence, []
    query = select(TraceStreamEvent).where(
        TraceStreamEvent.stream_sequence > after_sequence,
        TraceStreamEvent.stream_sequence <= observed_max,
    )
    if world_id is not None:
        query = query.where(TraceStreamEvent.world_id == world_id)
    if command_id is not None:
        query = query.where(TraceStreamEvent.command_id == command_id)
    records = session.scalars(
        query.order_by(TraceStreamEvent.stream_sequence).limit(limit)
    ).all()
    next_cursor = (
        records[-1].stream_sequence if len(records) == limit else observed_max
    )
    return next_cursor, [
        {
            "type": item.event_type,
            "event_id": item.event_id,
            "command_id": item.command_id,
            "world_id": item.world_id,
            "turn_id": item.turn_id,
            "sequence": item.span_sequence,
            "stream_sequence": item.stream_sequence,
            "provisional": False,
            "timestamp": item.created_at.isoformat(),
            "data": item.data_json,
        }
        for item in records
    ]
