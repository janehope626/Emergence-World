# 记录命令、跨度、模型交互、工具调用、事件和状态变化的完整执行链路。
"""Session-bound structured tracing for meaningful business boundaries."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
import inspect
from pathlib import Path
from time import monotonic
from types import TracebackType
from typing import Any, Callable, Iterator

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from emergence_world.db.base import utc_now
from emergence_world.db.models import (
    CommandExecution,
    ExecutionSpan,
    StateDiff,
    TraceStreamEvent,
)
from emergence_world.observability.redaction import redact
from emergence_world.observability.state_diff import SnapshotChange
from emergence_world.observability.stream import (
    trace_event_broker,
    trace_stream_event,
)

_current_tracer: ContextVar[TraceRecorder | None] = ContextVar("current_tracer", default=None)
_current_span_id: ContextVar[str | None] = ContextVar("current_span_id", default=None)


class SpanHandle:
    def __init__(self, span: ExecutionSpan) -> None:
        self.span = span

    def set_output(self, value: Any) -> None:
        output = redact(value)
        self.span.output_json = output if isinstance(output, dict) else {"value": output}

    def set_failed(self, error: str) -> None:
        self.span.status = "failed"
        self.span.error = str(redact(error))


class TraceRecorder:
    def __init__(
        self,
        session: Session,
        *,
        world_id: str,
        command_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> None:
        self.session = session
        self.command = CommandExecution(
            world_id=world_id,
            command_name=command_name,
            arguments_json=redact(arguments or {}),
            status="running",
        )
        session.add(self.command)
        session.flush()
        self._token: Token[TraceRecorder | None] | None = None
        self._failure_error: str | None = None
        self._turn_id: str | None = None

    def __enter__(self) -> TraceRecorder:
        self._token = _current_tracer.set(self)
        self.emit_event(
            "command.started", data={"name": self.command.command_name}
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        now = utc_now()
        self.command.completed_at = now
        if exc is None and self._failure_error is None:
            self.command.status = "completed"
        else:
            self.command.status = "failed"
            self.command.error = str(redact(str(exc or self._failure_error)))
        self.emit_event(
            "command.completed" if self.command.status == "completed" else "command.failed",
            data={"name": self.command.command_name, "status": self.command.status},
        )
        committed_event = trace_stream_event(
            "command.committed",
            command_id=self.command.id,
            world_id=self.command.world_id,
            turn_id=self._turn_id,
            provisional=False,
            data={"name": self.command.command_name, "status": self.command.status},
        )
        self._persist_stream_event(committed_event)
        pending = self.session.info.setdefault("trace_after_commit_events", [])
        if isinstance(pending, list):
            pending.append(committed_event)
        if self._token is not None:
            _current_tracer.reset(self._token)

    @contextmanager
    def span(
        self,
        *,
        stage: str,
        function: Callable[..., Any] | str,
        input: Any = None,
        turn_id: str | None = None,
    ) -> Iterator[SpanHandle]:
        function_name, source_file, source_line = _function_metadata(function)
        sequence = (
            self.session.scalar(
                select(func.max(ExecutionSpan.sequence_number)).where(
                    ExecutionSpan.command_id == self.command.id
                )
            )
            or 0
        ) + 1
        payload = redact(input if input is not None else {})
        record = ExecutionSpan(
            command_id=self.command.id,
            turn_id=turn_id,
            parent_span_id=_current_span_id.get(),
            sequence_number=sequence,
            stage=stage,
            function_name=function_name,
            source_file=source_file,
            source_line=source_line,
            input_json=payload if isinstance(payload, dict) else {"value": payload},
            status="running",
        )
        self.session.add(record)
        self.session.flush()
        self.emit_event(
            "span.started",
            turn_id=turn_id,
            sequence=sequence,
            data={"stage": stage, "function_name": function_name},
        )
        token = _current_span_id.set(record.id)
        started = monotonic()
        handle = SpanHandle(record)
        try:
            yield handle
        except Exception as exc:
            record.status = "failed"
            record.error = str(redact(str(exc)))
            raise
        else:
            if record.status == "running":
                record.status = "completed"
        finally:
            record.completed_at = utc_now()
            record.duration_ms = (monotonic() - started) * 1000
            self.emit_event(
                "span.completed" if record.status == "completed" else "span.failed",
                turn_id=record.turn_id,
                sequence=record.sequence_number,
                data={
                    "stage": record.stage,
                    "status": record.status,
                    "duration_ms": record.duration_ms,
                    "error": record.error,
                },
            )
            _current_span_id.reset(token)

    def bind_turn(self, turn_id: str) -> None:
        self._turn_id = turn_id
        for span in self.session.scalars(
            select(ExecutionSpan).where(
                ExecutionSpan.command_id == self.command.id,
                ExecutionSpan.turn_id.is_(None),
            )
        ):
            span.turn_id = turn_id
        for stream_event in self.session.scalars(
            select(TraceStreamEvent).where(
                TraceStreamEvent.command_id == self.command.id,
                TraceStreamEvent.turn_id.is_(None),
            )
        ):
            stream_event.turn_id = turn_id

    def mark_failed(self, error: str) -> None:
        self._failure_error = error

    def emit_event(
        self,
        event_type: str,
        *,
        turn_id: str | None = None,
        sequence: int | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        effective_turn_id = turn_id or self._turn_id
        stream_event = trace_stream_event(
            event_type,
            command_id=self.command.id,
            world_id=self.command.world_id,
            turn_id=effective_turn_id,
            sequence=sequence,
            data=redact(data or {}),
        )
        trace_event_broker.publish(stream_event)
        self._persist_stream_event(stream_event)

    def _persist_stream_event(self, stream_event: dict[str, Any]) -> None:
        data = stream_event.get("data", {})
        self.session.add(
            TraceStreamEvent(
                event_id=str(stream_event["event_id"]),
                command_id=self.command.id,
                world_id=self.command.world_id,
                turn_id=stream_event.get("turn_id"),
                event_type=str(stream_event["type"]),
                span_sequence=stream_event.get("sequence"),
                data_json=data if isinstance(data, dict) else {"value": data},
            )
        )

    def record_diffs(
        self, changes: tuple[SnapshotChange, ...], *, turn_id: str | None
    ) -> None:
        for sequence, change in enumerate(changes, start=1):
            self.session.add(
                StateDiff(
                    command_id=self.command.id,
                    turn_id=turn_id,
                    sequence_number=sequence,
                    entity_type=change.entity_type,
                    entity_id=change.entity_id,
                    path=change.path,
                    before_json=redact(change.before),
                    after_json=redact(change.after),
                )
            )
            self.emit_event(
                "state_diff.recorded",
                turn_id=turn_id,
                sequence=sequence,
                data={
                    "entity_type": change.entity_type,
                    "entity_id": change.entity_id,
                    "path": change.path,
                },
            )


def current_tracer() -> TraceRecorder | None:
    return _current_tracer.get()


def emit_trace_event(
    event_type: str,
    *,
    turn_id: str | None = None,
    sequence: int | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    recorder = current_tracer()
    if recorder is not None:
        recorder.emit_event(
            event_type, turn_id=turn_id, sequence=sequence, data=data
        )


@contextmanager
def traced_span(
    *,
    stage: str,
    function: Callable[..., Any] | str,
    input: Any = None,
    turn_id: str | None = None,
) -> Iterator[SpanHandle | None]:
    recorder = current_tracer()
    if recorder is None:
        yield None
        return
    with recorder.span(
        stage=stage, function=function, input=input, turn_id=turn_id
    ) as handle:
        yield handle


def _function_metadata(
    function: Callable[..., Any] | str,
) -> tuple[str, str | None, int | None]:
    if isinstance(function, str):
        return function, None, None
    name = f"{function.__module__}.{function.__qualname__}"
    try:
        raw_source_file = inspect.getsourcefile(function)
        source_file = _portable_source_path(raw_source_file)
        _, source_line = inspect.getsourcelines(function)
    except (OSError, TypeError):
        source_file, source_line = None, None
    return name, source_file, source_line


def _portable_source_path(source_file: str | None) -> str | None:
    """Store repository-relative paths so traces remain portable."""

    if source_file is None:
        return None
    path = Path(source_file).resolve()
    repository_root = Path(__file__).resolve().parents[3]
    try:
        return path.relative_to(repository_root).as_posix()
    except ValueError:
        return path.name


@event.listens_for(Session, "after_commit")
def _publish_committed_traces(session: Session) -> None:
    pending = session.info.pop("trace_after_commit_events", [])
    if not isinstance(pending, list):
        return
    for original in pending:
        if not isinstance(original, dict):
            continue
        committed = dict(original)
        committed["provisional"] = False
        trace_event_broker.publish(committed)


@event.listens_for(Session, "after_rollback")
def _publish_rolled_back_traces(session: Session) -> None:
    pending = session.info.pop("trace_after_commit_events", [])
    if not isinstance(pending, list):
        return
    for original in pending:
        if not isinstance(original, dict):
            continue
        trace_event_broker.publish(
            trace_stream_event(
                "command.rolled_back",
                command_id=original.get("command_id"),
                world_id=original.get("world_id"),
                provisional=False,
                data={"reason": "database_transaction_rolled_back"},
            )
        )
