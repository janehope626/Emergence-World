"""Acceptance tests for the deterministic Phase 1 world kernel."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from typer.testing import CliRunner

from emergence_world.cli import app
from emergence_world.db.models import Agent, AgentState, Turn, WorldEvent
from emergence_world.db.session import (
    create_sync_database_engine,
    create_sync_session_factory,
    sync_sqlite_url,
    sync_transaction,
)
from emergence_world.db.types import AgentStatus, TurnType
from emergence_world.seed import import_seed_bundle, load_seed_bundle
from emergence_world.tools import ManualToolExecutor
from emergence_world.world.scheduler import enqueue_boost
from emergence_world.world.runtime import step_world
from emergence_world.world.state import current_snapshot, replay_snapshot, snapshot_hash


def initialized_world(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    database = tmp_path / "world.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", sync_sqlite_url(database))
    command.upgrade(config, "head")
    engine = create_sync_database_engine(sync_sqlite_url(database))
    session_factory = create_sync_session_factory(engine)
    with sync_transaction(session_factory) as session:
        imported = import_seed_bundle(session, load_seed_bundle())
    return database, session_factory, imported.world_id


def test_round_robin_is_deterministic_and_skips_dead_agents(tmp_path: Path) -> None:
    _, session_factory, world_id = initialized_world(tmp_path)
    with sync_transaction(session_factory) as session:
        names = [step_world(session, world_id).agent_name for _ in range(3)]
        anvil = session.scalar(select(Agent).where(Agent.name == "Anvil"))
        assert anvil is not None
        state = session.get(AgentState, anvil.id)
        assert state is not None
        state.is_alive = False
        state.status = AgentStatus.DEAD
        next_name = step_world(session, world_id).agent_name

    assert names == ["Anchor", "Anvil", "Blackbox"]
    assert next_name == "Flora"


def test_boost_queue_runs_before_next_regular_turn(tmp_path: Path) -> None:
    _, session_factory, world_id = initialized_world(tmp_path)
    with sync_transaction(session_factory) as session:
        anchor = session.scalar(select(Agent).where(Agent.name == "Anchor"))
        assert anchor is not None
        enqueue_boost(session, world_id, anchor.id)
        boosted = step_world(session, world_id)
        regular = step_world(session, world_id)

    assert boosted.agent_name == "Anchor"
    assert boosted.turn_type == TurnType.BOOST.value
    assert regular.agent_name == "Anchor"
    assert regular.turn_type == TurnType.REGULAR.value


def test_time_decay_death_and_system_events_are_auditable(tmp_path: Path) -> None:
    _, session_factory, world_id = initialized_world(tmp_path)
    with sync_transaction(session_factory) as session:
        step_world(session, world_id, minutes=78 * 60)

    with session_factory() as session:
        states = session.scalars(
            select(AgentState).where(AgentState.world_id == world_id)
        ).all()
        event_types = session.scalars(
            select(WorldEvent.event_type)
            .where(WorldEvent.world_id == world_id)
            .order_by(WorldEvent.sequence_number)
        ).all()
        unattributed = session.scalar(
            select(func.count())
            .select_from(WorldEvent)
            .where(
                WorldEvent.world_id == world_id,
                WorldEvent.tool_call_id.is_(None),
                WorldEvent.system_rule.is_(None),
            )
        )

    assert all(
        not state.is_alive and state.status == AgentStatus.DEAD for state in states
    )
    assert event_types.count("clock_advanced") == 1
    assert event_types.count("needs_changed") == 10
    assert event_types.count("agent_died") == 10
    assert unattributed == 0


def test_event_replay_matches_current_projection(tmp_path: Path) -> None:
    _, session_factory, world_id = initialized_world(tmp_path)
    with sync_transaction(session_factory) as session:
        anchor = session.scalar(select(Agent).where(Agent.name == "Anchor"))
        assert anchor is not None
        enqueue_boost(session, world_id, anchor.id)
        step_world(session, world_id, minutes=60)
        step_world(session, world_id, minutes=60)
        assert snapshot_hash(current_snapshot(session, world_id)) == snapshot_hash(
            replay_snapshot(session, world_id)
        )


def test_replay_includes_audited_manual_tool_mutations(tmp_path: Path) -> None:
    _, session_factory, world_id = initialized_world(tmp_path)
    result = ManualToolExecutor(session_factory).call(
        agent_name="Anchor",
        tool_name="go_to_place",
        arguments={"place": "Town Hall"},
        world_id=world_id,
    )
    assert result.success

    with session_factory() as session:
        assert snapshot_hash(current_snapshot(session, world_id)) == snapshot_hash(
            replay_snapshot(session, world_id)
        )


def test_repeated_runs_have_matching_state_hashes(tmp_path: Path) -> None:
    hashes = []
    schedules = []
    for index in range(2):
        _, session_factory, world_id = initialized_world(tmp_path / str(index))
        with sync_transaction(session_factory) as session:
            for _ in range(25):
                step_world(session, world_id, minutes=30)
            hashes.append(snapshot_hash(current_snapshot(session, world_id)))
            schedules.append(
                [
                    agent.name
                    for agent in session.scalars(
                        select(Agent)
                        .join(Turn, Turn.agent_id == Agent.id)
                        .where(Turn.world_id == world_id)
                        .order_by(Turn.sequence_number)
                    )
                ]
            )

    assert hashes[0] == hashes[1]
    assert schedules[0] == schedules[1]
    assert len(schedules[0]) == len(schedules[1]) == 25


def test_cli_step_run_and_replay(tmp_path: Path) -> None:
    database = tmp_path / "cli-world.db"
    runner = CliRunner()

    assert runner.invoke(app, ["init", "--database", str(database)]).exit_code == 0
    stepped = runner.invoke(app, ["step", "--database", str(database)])
    run = runner.invoke(app, ["run", "--turns", "3", "--database", str(database)])
    replay = runner.invoke(app, ["replay", "--database", str(database)])

    assert stepped.exit_code == 0
    assert '"agent_name": "Anchor"' in stepped.stdout
    assert run.exit_code == 0
    assert '"turns": 3' in run.stdout
    assert replay.exit_code == 0
    assert '"matches": true' in replay.stdout


def test_cli_run_commits_progress_when_population_dies(tmp_path: Path) -> None:
    database = tmp_path / "death-run.db"
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--database", str(database)]).exit_code == 0

    run = runner.invoke(
        app,
        [
            "run",
            "--turns",
            "5",
            "--minutes",
            str(78 * 60),
            "--database",
            str(database),
        ],
    )
    replay = runner.invoke(app, ["replay", "--database", str(database)])

    assert run.exit_code == 0
    assert '"turns": 1' in run.stdout
    assert '"turns_requested": 5' in run.stdout
    assert replay.exit_code == 0


def test_cli_runs_and_inspects_autonomous_turn(tmp_path: Path) -> None:
    database = tmp_path / "autonomous-cli.db"
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--database", str(database)]).exit_code == 0

    run = runner.invoke(
        app,
        ["run-autonomous", "--turns", "2", "--provider", "scripted", "--database", str(database)],
    )
    assert run.exit_code == 0
    assert '"last_stop_reason": "provider_done"' in run.stdout

    session_factory = create_sync_session_factory(
        create_sync_database_engine(sync_sqlite_url(database))
    )
    with session_factory() as session:
        turn_id = session.scalar(select(Turn.id).order_by(Turn.sequence_number))
    assert turn_id is not None

    inspected = runner.invoke(app, ["inspect-turn", turn_id, "--database", str(database)])
    context = runner.invoke(app, ["inspect-context", turn_id, "--database", str(database)])
    responses = runner.invoke(
        app, ["inspect-provider-responses", turn_id, "--database", str(database)]
    )
    assert inspected.exit_code == context.exit_code == responses.exit_code == 0
    assert '"stop_reason": "provider_done"' in inspected.stdout
    assert '"context_hash":' in context.stdout
    assert '"raw_response":' in responses.stdout
