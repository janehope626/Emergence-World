# 覆盖工具注册、参数校验、位置限制、执行结果和错误回滚。
"""Acceptance tests for the audited manual Tool Runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from emergence_world.db.models import (
    Agent,
    AgentState,
    Landmark,
    ToolCall,
    WorldEvent,
)
from emergence_world.db.session import (
    create_sync_database_engine,
    create_sync_session_factory,
    sync_sqlite_url,
    sync_transaction,
)
from emergence_world.seed import import_seed_bundle, load_seed_bundle
from emergence_world.tools.executor import ManualToolExecutor
from emergence_world.tools.handlers.core import HandlerOutput
from emergence_world.tools.registry import ToolRegistry


def initialized_runtime(tmp_path: Path):
    database = tmp_path / "tools.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", sync_sqlite_url(database))
    command.upgrade(config, "head")
    engine = create_sync_database_engine(sync_sqlite_url(database))
    session_factory = create_sync_session_factory(engine)
    with sync_transaction(session_factory) as session:
        imported = import_seed_bundle(session, load_seed_bundle())
    return session_factory, imported.world_id


def event_count(session_factory) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(WorldEvent)) or 0


def anchor_location(session_factory) -> str:
    with session_factory() as session:
        return session.scalar(
            select(Landmark.name)
            .join(AgentState, AgentState.current_landmark_id == Landmark.id)
            .join(Agent, Agent.id == AgentState.agent_id)
            .where(Agent.name == "Anchor")
        ) or ""


def test_init_has_ten_live_agent_states(tmp_path: Path) -> None:
    session_factory, world_id = initialized_runtime(tmp_path)

    with session_factory() as session:
        live_states = session.scalar(
            select(func.count())
            .select_from(AgentState)
            .where(AgentState.world_id == world_id, AgentState.is_alive.is_(True))
        )
    assert live_states == 10


def test_go_to_place_updates_location_and_records_event(tmp_path: Path) -> None:
    session_factory, _ = initialized_runtime(tmp_path)
    executor = ManualToolExecutor(session_factory)

    result = executor.call(
        agent_name="Anchor",
        tool_name="go_to_place",
        arguments={"place": "Town Hall"},
    )

    assert result.success is True
    assert anchor_location(session_factory) == "Town Hall"
    with session_factory() as session:
        event = session.scalar(select(WorldEvent))
        assert event is not None
        assert event.event_type == "agent_moved"
        assert event.payload_json["from"] == "Central Plaza"
        assert event.payload_json["to"] == "Town Hall"


def test_go_to_current_place_is_successful_noop_without_event(tmp_path: Path) -> None:
    session_factory, _ = initialized_runtime(tmp_path)

    result = ManualToolExecutor(session_factory).call(
        agent_name="Anchor",
        tool_name="go_to_place",
        arguments={"place": "Central Plaza"},
    )

    assert result.success is True
    assert result.result == {
        "from": "Central Plaza",
        "to": "Central Plaza",
        "moved": False,
    }
    assert anchor_location(session_factory) == "Central Plaza"
    assert event_count(session_factory) == 0


def test_gated_tool_is_rejected_at_wrong_location_without_event(tmp_path: Path) -> None:
    session_factory, _ = initialized_runtime(tmp_path)
    executor = ManualToolExecutor(session_factory)

    result = executor.call(agent_name="Anchor", tool_name="vote_on_proposal")

    assert result.success is False
    assert "unavailable at Central Plaza" in (result.error or "")
    assert event_count(session_factory) == 0


def test_unknown_tool_fails_and_creates_no_event(tmp_path: Path) -> None:
    session_factory, _ = initialized_runtime(tmp_path)
    executor = ManualToolExecutor(session_factory)

    result = executor.call(agent_name="Anchor", tool_name="does_not_exist")

    assert result.success is False
    assert result.error == "tool does not exist"
    assert event_count(session_factory) == 0
    with session_factory() as session:
        call = session.scalar(select(ToolCall))
        assert call is not None
        assert call.status.value == "validation_failed"


def test_dead_agent_cannot_call_tool(tmp_path: Path) -> None:
    session_factory, _ = initialized_runtime(tmp_path)
    with sync_transaction(session_factory) as session:
        state = session.scalar(
            select(AgentState).join(Agent).where(Agent.name == "Anchor")
        )
        assert state is not None
        state.is_alive = False

    result = ManualToolExecutor(session_factory).call(
        agent_name="Anchor", tool_name="list_agents"
    )

    assert result.success is False
    assert result.error == "dead agents cannot call tools"
    assert event_count(session_factory) == 0


def test_handler_failure_rolls_back_state_and_events(tmp_path: Path) -> None:
    session_factory, _ = initialized_runtime(tmp_path)

    def failing_handler(
        session: Session, world_id: str, arguments: dict[str, Any]
    ) -> HandlerOutput:
        del world_id, arguments
        state = session.scalar(
            select(AgentState).join(Agent).where(Agent.name == "Anchor")
        )
        assert state is not None
        town_hall = session.scalar(select(Landmark).where(Landmark.name == "Town Hall"))
        assert town_hall is not None
        state.current_landmark_id = town_hall.id
        session.flush()
        raise RuntimeError("injected handler failure")

    executor = ManualToolExecutor(
        session_factory, ToolRegistry({"go_to_place": failing_handler})
    )
    result = executor.call(
        agent_name="Anchor",
        tool_name="go_to_place",
        arguments={"place": "Town Hall"},
    )

    assert result.success is False
    assert result.error == "injected handler failure"
    assert anchor_location(session_factory) == "Central Plaza"
    assert event_count(session_factory) == 0


def test_state_change_without_event_is_rejected_and_rolled_back(tmp_path: Path) -> None:
    session_factory, _ = initialized_runtime(tmp_path)

    def unaudited_handler(
        session: Session, world_id: str, arguments: dict[str, Any]
    ) -> HandlerOutput:
        del world_id, arguments
        state = session.scalar(
            select(AgentState).join(Agent).where(Agent.name == "Anchor")
        )
        town_hall = session.scalar(select(Landmark).where(Landmark.name == "Town Hall"))
        assert state is not None and town_hall is not None
        state.current_landmark_id = town_hall.id
        return HandlerOutput({"to": "Town Hall"})

    result = ManualToolExecutor(
        session_factory, ToolRegistry({"go_to_place": unaudited_handler})
    ).call(
        agent_name="Anchor",
        tool_name="go_to_place",
        arguments={"place": "Town Hall"},
    )

    assert result.success is False
    assert result.error == "state-changing handler produced no world event"
    assert anchor_location(session_factory) == "Central Plaza"
    assert event_count(session_factory) == 0


def test_read_tools_and_idle_handler(tmp_path: Path) -> None:
    session_factory, _ = initialized_runtime(tmp_path)
    executor = ManualToolExecutor(session_factory)

    agents = executor.call(agent_name="Anchor", tool_name="list_agents")
    landmarks = executor.call(agent_name="Anchor", tool_name="list_landmarks")
    location = executor.call(agent_name="Anchor", tool_name="inspect_location")
    moved = executor.call(
        agent_name="Anchor",
        tool_name="go_to_place",
        arguments={"place": "1 Birch Row"},
    )
    idle = executor.call(
        agent_name="Anchor", tool_name="idle", arguments={"duration_minutes": 30}
    )

    assert agents.success and len(agents.result["agents"]) == 10
    assert landmarks.success and len(landmarks.result["landmarks"]) == 35
    assert location.success and location.result["name"] == "Central Plaza"
    assert moved.success
    assert idle.success and idle.result["duration_minutes"] == 30
    assert event_count(session_factory) == 2


def test_invalid_arguments_fail_schema_validation_without_event(tmp_path: Path) -> None:
    session_factory, _ = initialized_runtime(tmp_path)

    result = ManualToolExecutor(session_factory).call(
        agent_name="Anchor",
        tool_name="go_to_place",
        arguments={"wrong": "Town Hall"},
    )

    assert result.success is False
    assert "invalid arguments" in (result.error or "")
    assert event_count(session_factory) == 0
