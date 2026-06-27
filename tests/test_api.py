"""Acceptance tests for the observability REST and WebSocket service."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import HTTPException, Request
import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from emergence_world.api import create_app
from emergence_world.api.routes.traces import (
    get_events,
    get_provider_interactions,
    get_spans,
    get_state_diffs,
    get_tool_calls,
    get_trace,
    list_traces,
)
from emergence_world.api.schemas.traces import TraceSummaryPage
from emergence_world.cli import app as cli_app
from emergence_world.db.models import CommandExecution, World
from emergence_world.db.session import (
    create_sync_database_engine,
    create_sync_session_factory,
    sync_sqlite_url,
    sync_transaction,
)
from emergence_world.observability.stream import (
    TraceEventBroker,
    trace_event_broker,
    trace_stream_event,
)
from emergence_world.world.runtime import step_world


def initialized_trace_database(tmp_path: Path) -> tuple[Path, str]:
    database = tmp_path / "api.db"
    runner = CliRunner()
    initialized = runner.invoke(cli_app, ["init", "--database", str(database)])
    assert initialized.exit_code == 0, initialized.stdout
    executed = runner.invoke(
        cli_app,
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
    assert executed.exit_code == 0, executed.stdout
    session_factory = create_sync_session_factory(
        create_sync_database_engine(sync_sqlite_url(database))
    )
    with session_factory() as session:
        command_id = session.scalar(select(CommandExecution.id))
        assert command_id is not None
    return database, command_id


def test_trace_rest_resources_are_bounded_and_payloads_require_auth(
    tmp_path: Path,
) -> None:
    database, command_id = initialized_trace_database(tmp_path)
    api = create_app(database, payload_access_token="test-payload-token", migrate=False)
    request = Request({"type": "http", "app": api, "headers": []})
    authorized_request = Request(
        {
            "type": "http",
            "app": api,
            "headers": [(b"x-trace-payload-token", b"test-payload-token")],
        }
    )
    session_factory = api.state.session_factory
    with session_factory() as session:
        summaries = list_traces(
            session,
            world_id=None,
            stage="provider",
            status_filter=None,
            started_from=None,
            started_to=None,
            offset=0,
            limit=1,
        )
        parsed_summaries = TraceSummaryPage.model_validate(summaries)
        assert parsed_summaries.items[0].id == command_id
        assert get_trace(command_id, session)["turn_id"] is not None

        spans = get_spans(
            command_id,
            request,
            session,
            stage="provider",
            status_filter=None,
            include_payloads=False,
            offset=0,
            limit=1,
        )
        assert len(spans["items"]) == 1
        assert "input" not in spans["items"][0]

        with pytest.raises(HTTPException) as forbidden:
            get_spans(
                command_id,
                request,
                session,
                stage=None,
                status_filter=None,
                include_payloads=True,
                offset=0,
                limit=1,
            )
        assert forbidden.value.status_code == 403
        expanded = get_spans(
            command_id,
            authorized_request,
            session,
            stage=None,
            status_filter=None,
            include_payloads=True,
            offset=0,
            limit=1,
        )
        assert expanded["items"][0]["input"] is not None

        assert get_provider_interactions(
            command_id,
            request,
            session,
            include_payloads=False,
            offset=0,
            limit=100,
        )["items"]
        assert get_tool_calls(command_id, session, offset=0, limit=100)["items"]
        get_events(command_id, session, offset=0, limit=100)
        diffs = get_state_diffs(command_id, session, offset=0, limit=2)
        assert len(diffs["items"]) == 2

    paths = api.openapi()["paths"]
    for path in (
        "/api/v1/traces",
        "/api/v1/traces/{command_id}",
        "/api/v1/traces/{command_id}/spans",
        "/api/v1/traces/{command_id}/provider-interactions",
        "/api/v1/traces/{command_id}/tool-calls",
        "/api/v1/traces/{command_id}/events",
        "/api/v1/traces/{command_id}/state-diffs",
    ):
        assert path in paths


def test_trace_stream_broker_receives_provisional_and_committed_events(
    tmp_path: Path,
) -> None:
    database = tmp_path / "stream.db"
    runner = CliRunner()
    assert runner.invoke(
        cli_app, ["init", "--database", str(database)]
    ).exit_code == 0
    api = create_app(database, migrate=False)
    assert any(route.path == "/ws/v1/traces" for route in api.routes)

    async def scenario() -> None:
        subscription = trace_event_broker.subscribe()
        session_factory = create_sync_session_factory(
            create_sync_database_engine(sync_sqlite_url(database))
        )

        def execute_step() -> None:
            with sync_transaction(session_factory) as session:
                world_id = session.scalar(select(World.id))
                assert world_id is not None
                step_world(session, world_id, command_name="api-stream-test")

        try:
            execute_step()
            await asyncio.sleep(0.1)
            received = []
            while not subscription.queue.empty():
                received.append(subscription.queue.get_nowait())

            assert received[0]["type"] == "command.started"
            assert any(item["type"] == "span.completed" for item in received)
            committed = next(
                item for item in received if item["type"] == "command.committed"
            )
            assert committed["provisional"] is False
        finally:
            trace_event_broker.unsubscribe(subscription)

    asyncio.run(scenario())


def test_trace_broker_reports_queue_gaps_and_removes_closed_loops() -> None:
    broker = TraceEventBroker(queue_size=2)

    async def overflow_scenario() -> None:
        subscription = broker.subscribe()
        for sequence in range(3):
            broker.publish(trace_stream_event("test", sequence=sequence))
        await asyncio.sleep(0)
        events = []
        while not subscription.queue.empty():
            events.append(subscription.queue.get_nowait())
        assert [item["type"] for item in events] == ["stream.gap", "test"]
        broker.unsubscribe(subscription)

    asyncio.run(overflow_scenario())
    assert broker.subscriber_count == 0

    loop = asyncio.new_event_loop()

    async def subscribe_once() -> None:
        broker.subscribe()

    loop.run_until_complete(subscribe_once())
    loop.close()
    assert broker.subscriber_count == 1
    broker.publish(trace_stream_event("test"))
    assert broker.subscriber_count == 0


def test_demo_trace_initializes_database_and_is_repeatable(tmp_path: Path) -> None:
    database = tmp_path / "demo.db"
    runner = CliRunner()

    first = runner.invoke(cli_app, ["demo-trace", "--database", str(database)])
    second = runner.invoke(cli_app, ["demo-trace", "--database", str(database)])

    assert first.exit_code == 0, first.stdout
    assert second.exit_code == 0, second.stdout
    session_factory = create_sync_session_factory(
        create_sync_database_engine(sync_sqlite_url(database))
    )
    with session_factory() as session:
        commands = session.scalars(
            select(CommandExecution).order_by(CommandExecution.started_at)
        ).all()
        assert len(commands) == 2
        assert all(command.status == "completed" for command in commands)
