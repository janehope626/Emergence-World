"""Structured execution tracing and state-difference utilities."""

from emergence_world.observability.state_diff import SnapshotChange, diff_snapshots
from emergence_world.observability.tracer import (
    TraceRecorder,
    current_tracer,
    emit_trace_event,
    traced_span,
)

__all__ = [
    "SnapshotChange",
    "TraceRecorder",
    "current_tracer",
    "diff_snapshots",
    "emit_trace_event",
    "traced_span",
]
