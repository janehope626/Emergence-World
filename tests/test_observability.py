"""Acceptance tests for structured execution tracing."""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

from sqlalchemy import select
from typer.testing import CliRunner

from emergence_world.cli import app
from emergence_world.db.base import utc_now
from emergence_world.db.models import (
    CommandExecution,
    ExecutionSpan,
    StateDiff,
    TraceStreamEvent,
    Turn,
)
from emergence_world.db.session import (
    create_sync_database_engine,
    create_sync_session_factory,
    sync_sqlite_url,
)
from emergence_world.observability.redaction import redact
from emergence_world.observability.state_diff import diff_snapshots
from emergence_world.observability.query import committed_trace_stream_events


def test_redaction_removes_nested_sensitive_values(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "value-secret")
    payload = {
        "api_key": "first-secret",
        "nested": {
            "Authorization": "second-secret",
            "safe": "visible value-secret",
        },
    }

    assert redact(payload) == {
        "api_key": "[REDACTED]",
        "nested": {"Authorization": "[REDACTED]", "safe": "visible [REDACTED]"},
    }


def test_snapshot_diff_produces_stable_agent_paths() -> None:
    before = {"agents": {"anchor": {"energy": 100, "mood": "neutral"}}}
    after = {"agents": {"anchor": {"energy": 95, "mood": "focused"}}}

    changes = diff_snapshots(before, after)

    assert [(item.entity_type, item.entity_id, item.path) for item in changes] == [
        ("agent", "anchor", "/energy"),
        ("agent", "anchor", "/mood"),
    ]


def test_autonomous_trace_links_provider_tool_event_and_state(tmp_path: Path) -> None:
    database = tmp_path / "trace.db"
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--database", str(database)]).exit_code == 0

    run = runner.invoke(
        app,
        [
            "run-autonomous",
            "--turns",
            "1",
            "--provider",
            "scripted",
            "--database",
            str(database),
        ],
    )
    assert run.exit_code == 0, run.stdout

    session_factory = create_sync_session_factory(
        create_sync_database_engine(sync_sqlite_url(database))
    )
    with session_factory() as session:
        command = session.scalar(select(CommandExecution))
        assert command is not None and command.status == "completed"
        spans = session.scalars(
            select(ExecutionSpan).order_by(ExecutionSpan.sequence_number)
        ).all()
        stages = [span.stage for span in spans]
        assert {
            "scheduler",
            "context",
            "provider",
            "tool_validation",
            "tool_handler",
            "event",
            "clock",
            "needs",
            "state_diff",
        } <= set(stages)
        assert all(span.status == "completed" for span in spans)
        assert all(span.turn_id is not None for span in spans)
        assert session.scalar(select(StateDiff)) is not None
        stream_events = session.scalars(
            select(TraceStreamEvent).order_by(TraceStreamEvent.stream_sequence)
        ).all()
        assert stream_events
        assert "tool.completed" in {item.event_type for item in stream_events}
        assert stream_events[-1].event_type == "command.committed"
        cursor, committed = committed_trace_stream_events(
            session, after_sequence=0
        )
        assert cursor == stream_events[-1].stream_sequence
        assert committed[-1]["type"] == "command.committed"
        assert all(item["provisional"] is False for item in committed)
        turn_id = session.scalar(select(Turn.id))
        assert turn_id is not None

    inspected = runner.invoke(
        app,
        ["inspect-trace", "--turn", turn_id, "--database", str(database)],
    )
    assert inspected.exit_code == 0, inspected.stdout
    assert '"provider_interactions"' in inspected.stdout
    assert '"tool_calls"' in inspected.stdout
    assert '"state_diffs"' in inspected.stdout

    summary = runner.invoke(
        app,
        [
            "list-traces",
            "--stage",
            "provider",
            "--limit",
            "1",
            "--database",
            str(database),
        ],
    )
    assert summary.exit_code == 0, summary.stdout
    summary_data = json.loads(summary.stdout)
    assert summary_data["limit"] == 1
    assert summary_data["items"][0]["span_count"] > 0
    assert "arguments" not in summary_data["items"][0]

    compact = runner.invoke(
        app,
        [
            "inspect-trace",
            "--turn",
            turn_id,
            "--stage",
            "provider",
            "--limit",
            "1",
            "--database",
            str(database),
        ],
    )
    assert compact.exit_code == 0, compact.stdout
    compact_data = json.loads(compact.stdout)
    assert compact_data["span_page"]["count"] == 1
    assert compact_data["spans"][0]["stage"] == "provider"
    assert "input" not in compact_data["spans"][0]
    assert "request" not in compact_data["provider_interactions"][0]

    expanded = runner.invoke(
        app,
        [
            "inspect-trace",
            "--turn",
            turn_id,
            "--stage",
            "provider",
            "--limit",
            "1",
            "--include-payloads",
            "--database",
            str(database),
        ],
    )
    assert expanded.exit_code == 0, expanded.stdout
    expanded_data = json.loads(expanded.stdout)
    assert "input" in expanded_data["spans"][0]
    assert "request" in expanded_data["provider_interactions"][0]

    with session_factory() as session:
        source_files = session.scalars(select(ExecutionSpan.source_file)).all()
        assert all(
            source_file is None or not Path(source_file).is_absolute()
            for source_file in source_files
        )


def test_failed_manual_tool_is_traced_without_world_state_diff(tmp_path: Path) -> None:
    database = tmp_path / "failed-trace.db"
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--database", str(database)]).exit_code == 0

    failed = runner.invoke(
        app,
        [
            "call-tool",
            "Anchor",
            "missing_tool",
            "--database",
            str(database),
        ],
    )
    assert failed.exit_code == 1

    session_factory = create_sync_session_factory(
        create_sync_database_engine(sync_sqlite_url(database))
    )
    with session_factory() as session:
        command = session.scalar(select(CommandExecution))
        assert command is not None and command.status == "failed"
        validation = session.scalar(
            select(ExecutionSpan).where(ExecutionSpan.stage == "tool_validation")
        )
        assert validation is not None
        assert validation.output_json == {
            "valid": False,
            "error": "tool does not exist",
        }
        assert validation.status == "failed"
        assert session.scalar(select(StateDiff)) is None


def test_trace_retention_dry_run_and_execute_preserve_latest(tmp_path: Path) -> None:
    database = tmp_path / "retention.db"
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--database", str(database)]).exit_code == 0
    for _ in range(2):
        result = runner.invoke(
            app,
            ["step", "--database", str(database)],
        )
        assert result.exit_code == 0, result.stdout

    session_factory = create_sync_session_factory(
        create_sync_database_engine(sync_sqlite_url(database))
    )
    with session_factory.begin() as session:
        commands = session.scalars(
            select(CommandExecution).order_by(CommandExecution.started_at)
        ).all()
        assert len(commands) == 2
        for command in commands:
            command.started_at = utc_now() - timedelta(days=60)
        old_command_id = commands[0].id
        latest_command_id = commands[1].id

    dry_run = runner.invoke(
        app,
        [
            "prune-traces",
            "--older-than-days",
            "30",
            "--keep-latest",
            "1",
            "--database",
            str(database),
        ],
    )
    assert dry_run.exit_code == 0, dry_run.stdout
    dry_run_data = json.loads(dry_run.stdout)
    assert dry_run_data["dry_run"] is True
    assert dry_run_data["candidate_command_ids"] == [old_command_id]

    executed = runner.invoke(
        app,
        [
            "prune-traces",
            "--older-than-days",
            "30",
            "--keep-latest",
            "1",
            "--execute",
            "--database",
            str(database),
        ],
    )
    assert executed.exit_code == 0, executed.stdout
    assert json.loads(executed.stdout)["deleted_count"] == 1
    with session_factory() as session:
        remaining = session.scalars(select(CommandExecution.id)).all()
        assert remaining == [latest_command_id]
