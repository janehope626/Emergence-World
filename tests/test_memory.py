# 覆盖记忆写入、检索、摘要、日记、灵魂条目与关系修订。
"""Phase-one acceptance tests for private deterministic memory."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from emergence_world.agents.memory_context import build_memory_context
from emergence_world.db.models import (
    Agent,
    ConversationRecord,
    DiaryRevision,
    EpisodicMemory,
    MemorySummarySource,
    Relationship,
    WorldEvent,
)
from emergence_world.db.session import (
    create_sync_database_engine,
    create_sync_session_factory,
    sync_sqlite_url,
    sync_transaction,
)
from emergence_world.seed import import_seed_bundle, load_seed_bundle
from emergence_world.tools import ManualToolExecutor
from emergence_world.world.state import current_snapshot, replay_snapshot


def memory_world(tmp_path: Path):
    database = tmp_path / "memory.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", sync_sqlite_url(database))
    command.upgrade(config, "head")
    engine = create_sync_database_engine(sync_sqlite_url(database))
    sessions = create_sync_session_factory(engine)
    with sync_transaction(sessions) as session:
        imported = import_seed_bundle(session, load_seed_bundle())
    return sessions, imported.world_id


def agent_id(sessions, world_id: str, name: str) -> str:
    with sessions() as session:
        value = session.scalar(
            select(Agent.id).where(Agent.world_id == world_id, Agent.name == name)
        )
        assert value is not None
        return value


def add_memories(tools: ManualToolExecutor, world_id: str, count: int) -> None:
    for index in range(count):
        result = tools.call(
            agent_name="Anchor",
            tool_name="add_to_longterm_memory",
            arguments={
                "content": f"Observation {index} about orchard planning",
                "importance": index / max(count, 1),
                "tags": ["orchard", "planning"],
            },
            world_id=world_id,
        )
        assert result.success


def test_longterm_memory_event_replay_and_private_reads(tmp_path: Path) -> None:
    sessions, world_id = memory_world(tmp_path)
    tools = ManualToolExecutor(sessions)
    result = tools.call(
        agent_name="Flora",
        tool_name="add_to_longterm_memory",
        arguments={"content": "private greenhouse codeword", "tags": ["greenhouse"]},
        world_id=world_id,
    )
    assert result.success
    anchor_read = tools.call(
        agent_name="Anchor",
        tool_name="retrieve_specific_memories",
        arguments={"query": "greenhouse"},
        world_id=world_id,
    )
    assert anchor_read.success and anchor_read.result["memories"] == []
    with sessions() as session:
        event = session.scalar(
            select(WorldEvent).where(WorldEvent.event_type == "memory_added")
        )
        assert event is not None and event.payload_json["content"] == "private greenhouse codeword"
        current = current_snapshot(session, world_id)
        replayed = replay_snapshot(session, world_id)
        assert current["memory"] == replayed["memory"]


def test_soul_context_diary_revisions_and_stable_context_hash(tmp_path: Path) -> None:
    sessions, world_id = memory_world(tmp_path)
    tools = ManualToolExecutor(sessions)
    assert tools.call(
        agent_name="Anchor",
        tool_name="add_to_soul",
        arguments={"content": "I mediate before I judge."},
        world_id=world_id,
    ).success
    for content in ["First reflection", "Revised reflection"]:
        assert tools.call(
            agent_name="Anchor",
            tool_name="write_diary",
            arguments={"content": content},
            world_id=world_id,
        ).success
    anchor_id = agent_id(sessions, world_id, "Anchor")
    with sync_transaction(sessions) as session:
        first = build_memory_context(
            session, world_id=world_id, agent_id=anchor_id, query="reflection"
        )
        second = build_memory_context(
            session, world_id=world_id, agent_id=anchor_id, query="reflection"
        )
        assert first.context_hash == second.context_hash
        assert first.context["soul"][0]["content"] == "I mediate before I judge."
        assert first.context["diary"][0]["content"] == "Revised reflection"
        assert session.scalar(select(func.count()).select_from(DiaryRevision)) == 2


def test_conversations_are_automatic_and_relationships_directional(
    tmp_path: Path,
) -> None:
    sessions, world_id = memory_world(tmp_path)
    tools = ManualToolExecutor(sessions)
    assert not tools.call(
        agent_name="Anchor", tool_name="conversation_recorded", world_id=world_id
    ).success
    assert tools.call(
        agent_name="Anchor",
        tool_name="send_message",
        arguments={"target": "Flora", "content": "A private message."},
        world_id=world_id,
    ).success
    assert tools.call(
        agent_name="Anchor",
        tool_name="assign_relationship",
        arguments={
            "target": "Flora",
            "relationship_type": "trusted",
            "rationale": "Reliable collaborator",
        },
        world_id=world_id,
    ).success
    anchor = agent_id(sessions, world_id, "Anchor")
    flora = agent_id(sessions, world_id, "Flora")
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ConversationRecord)) == 2
        assert session.scalar(
            select(Relationship).where(
                Relationship.observer_agent_id == anchor,
                Relationship.target_agent_id == flora,
            )
        ) is not None
        assert session.scalar(
            select(Relationship).where(
                Relationship.observer_agent_id == flora,
                Relationship.target_agent_id == anchor,
            )
        ) is None


def test_self_care_threshold_archive_sources_and_replay(tmp_path: Path) -> None:
    sessions, world_id = memory_world(tmp_path)
    tools = ManualToolExecutor(sessions)
    add_memories(tools, world_id, 29)
    assert tools.call(
        agent_name="Anchor",
        tool_name="go_to_place",
        arguments={"place": "1 Birch Row"},
        world_id=world_id,
    ).success
    failed = tools.call(agent_name="Anchor", tool_name="self_care", world_id=world_id)
    assert not failed.success and "at least 30" in (failed.error or "")
    assert tools.call(
        agent_name="Anchor",
        tool_name="add_to_longterm_memory",
        arguments={"content": "Observation 29 about orchard planning"},
        world_id=world_id,
    ).success
    result = tools.call(agent_name="Anchor", tool_name="self_care", world_id=world_id)
    assert result.success and result.result["source_count"] == 30
    with sessions() as session:
        assert session.scalar(
            select(func.count())
            .select_from(EpisodicMemory)
            .where(EpisodicMemory.active.is_(False))
        ) == 30
        assert session.scalar(
            select(func.count()).select_from(MemorySummarySource)
        ) == 30
        current = current_snapshot(session, world_id)
        replayed = replay_snapshot(session, world_id)
        assert current["memory"] == replayed["memory"]


def test_self_care_requires_assigned_home(tmp_path: Path) -> None:
    sessions, world_id = memory_world(tmp_path)
    tools = ManualToolExecutor(sessions)
    add_memories(tools, world_id, 30)
    assert tools.call(
        agent_name="Anchor",
        tool_name="go_to_place",
        arguments={"place": "2 Birch Row"},
        world_id=world_id,
    ).success
    result = tools.call(agent_name="Anchor", tool_name="self_care", world_id=world_id)
    assert not result.success
    assert "assigned home" in (result.error or "")
