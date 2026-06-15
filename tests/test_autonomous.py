"""Acceptance tests for complete autonomous turn closure."""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import func, select

from emergence_world.agents.models import AgentDecision, RequestedToolCall
from emergence_world.agents.providers.scripted import ScriptedProvider
from emergence_world.agents.providers.recording import (
    RecordedProviderFailure,
    RecordingProvider,
)
from emergence_world.agents.providers.openai import OpenAIProvider, OpenAIProviderConfig
from emergence_world.agents.providers.smoke import ProviderFailure, ProviderFailureCode
from emergence_world.agents.providers.smoke import ProviderSmokeConfig
from emergence_world.cli import run_one_autonomous_turn
from emergence_world.db.models import (
    Agent,
    AgentState,
    ProviderInteraction,
    SimulationClock,
    ToolCall,
    Turn,
    WorldEvent,
)
from emergence_world.db.session import (
    create_sync_database_engine,
    create_sync_session_factory,
    sync_sqlite_url,
    sync_transaction,
)
from emergence_world.db.types import AgentStatus, ToolCallStatus, TurnStatus
from emergence_world.seed import import_seed_bundle, load_seed_bundle
from emergence_world.world.runtime import autonomous_step_world
from emergence_world.world.events import append_system_event
from emergence_world.world.state import current_snapshot, replay_snapshot, snapshot_hash


def initialized_world(tmp_path: Path):
    database = tmp_path / "autonomous.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", sync_sqlite_url(database))
    command.upgrade(config, "head")
    engine = create_sync_database_engine(sync_sqlite_url(database))
    session_factory = create_sync_session_factory(engine)
    with sync_transaction(session_factory) as session:
        imported = import_seed_bundle(session, load_seed_bundle())
    return session_factory, imported.world_id


def provider(*decisions: AgentDecision) -> ScriptedProvider:
    return ScriptedProvider(decisions)


def call(call_id: str, tool: str, **arguments: object) -> RequestedToolCall:
    return RequestedToolCall(call_id=call_id, tool_name=tool, arguments=arguments)


def run_step(session_factory, world_id: str, scripted: ScriptedProvider):
    async def scenario():
        with sync_transaction(session_factory) as session:
            return await autonomous_step_world(session, world_id, scripted)

    return asyncio.run(scenario())


def test_scripted_provider_runs_twenty_successful_autonomous_turns(
    tmp_path: Path,
) -> None:
    session_factory, world_id = initialized_world(tmp_path)
    with sync_transaction(session_factory) as session:
        clock = session.get(SimulationClock, world_id)
        assert clock is not None
        for agent, state in session.execute(
            select(Agent, AgentState)
            .join(AgentState, AgentState.agent_id == Agent.id)
            .where(Agent.world_id == world_id, Agent.name != "Anchor")
        ):
            state.is_alive = False
            state.status = AgentStatus.DEAD
            append_system_event(
                session,
                world_id=world_id,
                system_rule="test_population_setup",
                event_type="agent_died",
                payload={"agent_id": agent.id},
                simulation_time=clock.current_time,
            )

    for index in range(20):
        result = run_step(
            session_factory,
            world_id,
            provider(
                AgentDecision(
                    tool_calls=(call(str(index), "go_to_place", place="Central Plaza"),)
                ),
                AgentDecision(terminate=True),
            ),
        )
        assert result.stop_reason == "provider_done"

    with session_factory() as session:
        calls = session.scalars(
            select(ToolCall).where(ToolCall.world_id == world_id)
        ).all()
        turns = session.scalars(
            select(Turn).where(Turn.world_id == world_id).order_by(Turn.sequence_number)
        ).all()
        interactions = session.scalars(
            select(ProviderInteraction).where(ProviderInteraction.world_id == world_id)
        ).all()
        assert len(calls) == 20
        assert all(item.status == ToolCallStatus.SUCCEEDED for item in calls)
        assert len(turns) == 20
        assert len({item.agent_id for item in turns}) == 1
        assert all(item.context_hash and item.context_version for item in turns)
        assert len(interactions) == 40
        assert all(item.raw_response_json is not None for item in interactions)
        assert snapshot_hash(current_snapshot(session, world_id)) == snapshot_hash(
            replay_snapshot(session, world_id)
        )


def test_autonomous_tool_failure_is_returned_and_turn_continues(tmp_path: Path) -> None:
    session_factory, world_id = initialized_world(tmp_path)
    result = run_step(
        session_factory,
        world_id,
        provider(
            AgentDecision(tool_calls=(call("bad", "does_not_exist"),)),
            AgentDecision(
                tool_calls=(call("good", "go_to_place", place="Central Plaza"),)
            ),
            AgentDecision(terminate=True),
        ),
    )

    assert result.stop_reason == "provider_done"
    with session_factory() as session:
        calls = session.scalars(
            select(ToolCall).where(ToolCall.turn_id == result.turn_id).order_by(ToolCall.sequence_number)
        ).all()
        requests = session.scalars(
            select(ProviderInteraction)
            .where(ProviderInteraction.turn_id == result.turn_id)
            .order_by(ProviderInteraction.sequence_number)
        ).all()
        assert [item.status for item in calls] == [
            ToolCallStatus.VALIDATION_FAILED,
            ToolCallStatus.SUCCEEDED,
        ]
        assert requests[1].request_json["prior_results"][0]["success"] is False


def test_max_tool_calls_terminates_turn(tmp_path: Path) -> None:
    session_factory, world_id = initialized_world(tmp_path)
    calls = tuple(call(str(index), "list_agents") for index in range(35))

    result = run_step(
        session_factory, world_id, provider(AgentDecision(tool_calls=calls))
    )

    assert result.stop_reason == "max_tool_calls_reached"
    with session_factory() as session:
        turn = session.get(Turn, result.turn_id)
        assert turn is not None
        assert turn.tool_calls_used == turn.tool_call_budget == 30


def test_dead_agent_is_not_scheduled_for_autonomous_turn(tmp_path: Path) -> None:
    session_factory, world_id = initialized_world(tmp_path)
    with sync_transaction(session_factory) as session:
        anchor = session.scalar(select(Agent).where(Agent.name == "Anchor"))
        assert anchor is not None
        state = session.get(AgentState, anchor.id)
        assert state is not None
        state.is_alive = False
        state.status = AgentStatus.DEAD

    result = run_step(
        session_factory,
        world_id,
        provider(AgentDecision(tool_calls=(call("idle", "idle"),), terminate=True)),
    )

    assert result.agent_name == "Anvil"


def test_autonomous_step_adds_world_events_and_provider_audit(tmp_path: Path) -> None:
    session_factory, world_id = initialized_world(tmp_path)
    with session_factory() as session:
        before = session.scalar(select(func.count()).select_from(WorldEvent)) or 0

    result = run_step(
        session_factory,
        world_id,
        provider(
            AgentDecision(
                tool_calls=(call("move", "go_to_place", place="Town Hall"),),
                terminate=True,
            )
        ),
    )

    with session_factory() as session:
        after = session.scalar(select(func.count()).select_from(WorldEvent)) or 0
        turn = session.get(Turn, result.turn_id)
        audit = session.scalar(
            select(ProviderInteraction).where(ProviderInteraction.turn_id == result.turn_id)
        )
        assert after > before
        assert turn is not None and turn.context_hash
        assert turn.stop_reason == "provider_done"
        assert audit is not None and audit.raw_response_json is not None


def test_recording_provider_parse_failure_is_audited_and_secrets_are_redacted(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    secret = "smoke-secret-must-not-persist"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    session_factory, world_id = initialized_world(tmp_path)
    provider = RecordingProvider(
        [
            f'{{"reasoning_text":"{secret}"',
        ]
    )

    with session_factory() as session, session.begin():
        with pytest.raises(ProviderFailure) as captured:
            asyncio.run(autonomous_step_world(session, world_id, provider))
    assert captured.value.code == ProviderFailureCode.INVALID_JSON

    with session_factory() as session:
        audit = session.scalar(select(ProviderInteraction))
        assert audit is not None
        assert audit.parse_error is not None
        assert audit.parse_error.startswith("invalid_json")
        assert secret not in str(audit.request_json)
        assert secret not in str(audit.raw_response_json)
        assert secret not in str(audit.parse_error)
        assert secret not in caplog.text


def test_provider_failure_message_is_redacted_from_audit(
    tmp_path: Path, monkeypatch
) -> None:
    secret = "provider-error-secret"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    session_factory, world_id = initialized_world(tmp_path)
    provider = RecordingProvider(
        [
            RecordedProviderFailure(
                ProviderFailureCode.PROVIDER_ERROR,
                f"upstream error included {secret}",
                raw_response={"error": secret},
            )
        ]
    )

    with session_factory() as session, session.begin():
        with pytest.raises(ProviderFailure):
            asyncio.run(autonomous_step_world(session, world_id, provider))

    with session_factory() as session:
        audit = session.scalar(select(ProviderInteraction))
        assert audit is not None
        assert secret not in str(audit.raw_response_json)
        assert secret not in str(audit.parse_error)


def test_cli_turn_boundary_persists_provider_failure_audit(tmp_path: Path) -> None:
    session_factory, world_id = initialized_world(tmp_path)
    provider = RecordingProvider(
        [RecordedProviderFailure(ProviderFailureCode.TIMEOUT, "offline timeout")]
    )

    with pytest.raises(ProviderFailure):
        asyncio.run(run_one_autonomous_turn(session_factory, world_id, provider, 30))

    with session_factory() as session:
        turn = session.scalar(select(Turn))
        audit = session.scalar(select(ProviderInteraction))
        assert turn is not None
        assert turn.status == TurnStatus.FAILED
        assert turn.stop_reason == "provider_failure:timeout"
        assert audit is not None
        assert audit.parse_error == "timeout: offline timeout"


def test_openai_provider_mock_response_is_fully_audited(
    tmp_path: Path, monkeypatch
) -> None:
    secret = "openai-audit-secret"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    session_factory, world_id = initialized_world(tmp_path)

    class Response:
        def model_dump(self, *, mode: str):
            assert mode == "json"
            return {
                "id": "response-1",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Done."}],
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                },
            }

    class Responses:
        async def create(self, **kwargs):
            assert secret not in str(kwargs)
            return Response()

    class Client:
        responses = Responses()

    provider = OpenAIProvider(
        OpenAIProviderConfig(
            model="mock-model",
            smoke_config=ProviderSmokeConfig(max_turns=1),
            input_cost_per_million_tokens_usd=1,
            output_cost_per_million_tokens_usd=2,
        ),
        client=Client(),
    )

    async def scenario():
        with sync_transaction(session_factory) as session:
            return await autonomous_step_world(session, world_id, provider)

    result = asyncio.run(scenario())

    with session_factory() as session:
        audit = session.scalar(
            select(ProviderInteraction).where(ProviderInteraction.turn_id == result.turn_id)
        )
        assert audit is not None
        assert audit.provider == "openai"
        assert audit.raw_response_json["id"] == "response-1"
        assert audit.total_tokens == 120
        assert audit.cost_usd == pytest.approx(0.00014)
        assert secret not in str(audit.request_json)
        assert secret not in str(audit.raw_response_json)
