# 覆盖数据库模型约束、关系、事务和迁移后的基础行为。
"""Database portability, migration, and invariant tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from emergence_world.db.models import (
    Agent,
    CreditLedgerEntry,
    Experiment,
    ToolCall,
    Turn,
    World,
    WorldEvent,
)
from emergence_world.db.session import (
    create_sync_database_engine,
    create_sync_session_factory,
    sync_sqlite_url,
    sync_transaction,
)
from emergence_world.db.types import ToolCallStatus, TurnType


def alembic_config(database_path: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", sync_sqlite_url(database_path))
    return config


def migrated_database(tmp_path: Path) -> tuple[sessionmaker[Session], Path]:
    database_path = tmp_path / "world.db"
    command.upgrade(alembic_config(database_path), "head")
    engine = create_sync_database_engine(sync_sqlite_url(database_path))
    return create_sync_session_factory(engine), database_path


def seed_world(session: Session) -> tuple[World, Agent]:
    experiment = Experiment(
        name="test experiment",
        config_version="1.0",
        random_seed=7,
    )
    session.add(experiment)
    session.flush()
    world = World(experiment_id=experiment.id, name="test world")
    session.add(world)
    session.flush()
    agent = Agent(
        world_id=world.id,
        name="Anchor",
        role="Conflict Mediator",
        personality="Challenges complacency.",
        north_star_goal="Conflict generates growth.",
        profile_version="0.01",
    )
    session.add(agent)
    session.flush()
    return world, agent


def test_alembic_upgrade_and_downgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    config = alembic_config(database_path)

    command.upgrade(config, "head")
    engine = create_sync_database_engine(sync_sqlite_url(database_path))
    tables = set(inspect(engine).get_table_names())

    assert {
        "experiments",
        "experiment_runs",
        "worlds",
        "constitution_articles",
        "seed_documents",
        "simulation_clocks",
        "agents",
        "agent_states",
        "landmarks",
        "turns",
        "boost_turn_requests",
        "tool_definitions",
        "tool_calls",
        "world_events",
        "credit_ledger_entries",
        "soul_entries",
        "soul_entry_revisions",
        "episodic_memories",
        "diary_entries",
        "diary_revisions",
        "conversation_records",
        "relationships",
        "relationship_revisions",
        "memory_summaries",
        "memory_summary_sources",
        "context_builds",
        "context_memory_candidates",
        "context_memory_selections",
        "provider_interactions",
        "command_executions",
        "execution_spans",
        "state_diffs",
        "trace_stream_events",
    } <= tables

    engine.dispose()
    command.check(config)
    command.downgrade(config, "base")
    engine = create_sync_database_engine(sync_sqlite_url(database_path))
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    engine.dispose()


def test_foreign_keys_are_enabled_for_sqlite(tmp_path: Path) -> None:
    session_factory, _ = migrated_database(tmp_path)

    with pytest.raises(IntegrityError):
        with sync_transaction(session_factory) as session:
            session.add(World(experiment_id="missing", name="invalid"))


def test_failed_transaction_leaves_no_partial_writes(tmp_path: Path) -> None:
    session_factory, _ = migrated_database(tmp_path)

    with pytest.raises(RuntimeError, match="abort"):
        with sync_transaction(session_factory) as session:
            session.add(
                Experiment(name="rolled back", config_version="1.0", random_seed=1)
            )
            session.flush()
            raise RuntimeError("abort")

    with session_factory() as session:
        count = session.scalar(select(func.count()).select_from(Experiment))
    assert count == 0


def test_world_event_requires_exactly_one_attribution(tmp_path: Path) -> None:
    session_factory, _ = migrated_database(tmp_path)

    with pytest.raises(IntegrityError):
        with sync_transaction(session_factory) as session:
            world, _ = seed_world(session)
            session.add(
                WorldEvent(
                    world_id=world.id,
                    sequence_number=1,
                    event_type="invalid",
                    payload_json={},
                    simulation_time=datetime.now(UTC),
                )
            )


def test_credit_balance_is_reconstructed_from_ledger(tmp_path: Path) -> None:
    session_factory, _ = migrated_database(tmp_path)

    with sync_transaction(session_factory) as session:
        world, agent = seed_world(session)
        session.add_all(
            [
                CreditLedgerEntry(
                    world_id=world.id,
                    agent_id=agent.id,
                    system_rule="initial_credit_grant",
                    amount=10,
                    reason="initial balance",
                    simulation_time=datetime.now(UTC),
                ),
                CreditLedgerEntry(
                    world_id=world.id,
                    agent_id=agent.id,
                    system_rule="research_grant",
                    amount=4,
                    reason="grant",
                    simulation_time=datetime.now(UTC),
                ),
                CreditLedgerEntry(
                    world_id=world.id,
                    agent_id=agent.id,
                    system_rule="maintenance_charge",
                    amount=-3,
                    reason="charge",
                    simulation_time=datetime.now(UTC),
                ),
            ]
        )
        agent_id = agent.id

    with session_factory() as session:
        balance = session.scalar(
            select(func.sum(CreditLedgerEntry.amount)).where(
                CreditLedgerEntry.agent_id == agent_id
            )
        )
    assert balance == 11


def test_cross_world_event_references_are_rejected(tmp_path: Path) -> None:
    session_factory, _ = migrated_database(tmp_path)

    with pytest.raises(IntegrityError):
        with sync_transaction(session_factory) as session:
            first_world, first_agent = seed_world(session)
            experiment = Experiment(
                name="second experiment", config_version="1.0", random_seed=8
            )
            session.add(experiment)
            session.flush()
            second_world = World(experiment_id=experiment.id, name="second world")
            session.add(second_world)
            session.flush()
            turn = Turn(
                world_id=first_world.id,
                agent_id=first_agent.id,
                sequence_number=1,
                turn_type=TurnType.REGULAR,
                tool_call_budget=30,
            )
            session.add(turn)
            session.flush()
            tool_call = ToolCall(
                world_id=first_world.id,
                turn_id=turn.id,
                agent_id=first_agent.id,
                sequence_number=1,
                tool_name="idle",
                tool_version="1.0",
                arguments_json={},
                status=ToolCallStatus.SUCCEEDED,
            )
            session.add(tool_call)
            session.flush()
            session.add(
                WorldEvent(
                    world_id=second_world.id,
                    tool_call_id=tool_call.id,
                    sequence_number=1,
                    event_type="cross_world_event",
                    payload_json={},
                    simulation_time=datetime.now(UTC),
                )
            )
