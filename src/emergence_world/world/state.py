# 从数据库构建当前世界快照，并通过事件重放验证确定性状态。
"""Canonical current-state snapshots, hashing, and event replay."""

from __future__ import annotations

from hashlib import sha256
from json import dumps
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from emergence_world.db.models import (
    Agent,
    AgentState,
    BoostTurnRequest,
    ConversationRecord,
    DiaryEntry,
    DiaryRevision,
    EpisodicMemory,
    Landmark,
    MemorySummary,
    MemorySummarySource,
    ReactionRequest,
    Relationship,
    SimulationClock,
    SoulEntry,
    World,
    WorldEvent,
)
from emergence_world.world.events import apply_event


def current_snapshot(session: Session, world_id: str) -> dict[str, Any]:
    clock = session.get(SimulationClock, world_id)
    if clock is None:
        raise ValueError("simulation clock not found")
    rows = session.execute(
        select(Agent, AgentState, Landmark)
        .join(AgentState, AgentState.agent_id == Agent.id)
        .outerjoin(Landmark, Landmark.id == AgentState.current_landmark_id)
        .where(Agent.world_id == world_id)
        .order_by(Agent.id)
    ).all()
    boosts = session.execute(
        select(BoostTurnRequest, Agent)
        .join(Agent, Agent.id == BoostTurnRequest.agent_id)
        .where(
            BoostTurnRequest.world_id == world_id,
            BoostTurnRequest.consumed.is_(False),
        )
        .order_by(BoostTurnRequest.sequence_number)
    ).all()
    reactions = session.execute(
        select(ReactionRequest, Agent)
        .join(Agent, Agent.id == ReactionRequest.agent_id)
        .where(
            ReactionRequest.world_id == world_id,
            ReactionRequest.consumed.is_(False),
        )
        .order_by(ReactionRequest.sequence_number)
    ).all()
    snapshot: dict[str, Any] = {
        "simulation_time": clock.current_time.isoformat(),
        "pending_reactions": [
            {
                "agent_id": request.agent_id,
                "agent_name": agent.name,
                "queue_sequence": request.sequence_number,
            }
            for request, agent in reactions
        ],
        "pending_boosts": [
            {
                "agent_id": request.agent_id,
                "agent_name": agent.name,
                "queue_sequence": request.sequence_number,
            }
            for request, agent in boosts
        ],
        "agents": {
            agent.id: {
                "name": agent.name,
                "location": landmark.name if landmark else None,
                "mood": state.mood,
                "status": state.status.value,
                "is_alive": state.is_alive,
                "energy": state.energy,
                "knowledge": state.knowledge,
                "influence": state.influence,
                "credits": state.cached_credit_balance,
                "zero_energy_since": (
                    state.zero_energy_since.isoformat()
                    if state.zero_energy_since is not None
                    else None
                ),
            }
            for agent, state, landmark in rows
        },
        "memory": {agent.id: _empty_memory_state() for agent, _, _ in rows},
    }
    for episodic in session.scalars(
        select(EpisodicMemory).where(EpisodicMemory.world_id == world_id)
    ):
        snapshot["memory"][episodic.agent_id]["episodic"][episodic.id] = {
            "content": episodic.content,
            "importance": episodic.importance,
            "tags": episodic.tags_json,
            "active": episodic.active,
            "archived_at": episodic.archived_at.isoformat() if episodic.archived_at else None,
        }
    for soul in session.scalars(select(SoulEntry).where(SoulEntry.world_id == world_id)):
        snapshot["memory"][soul.agent_id]["soul"][soul.id] = {
            "content": soul.current_content,
            "active": soul.active,
        }
    for diary in session.scalars(
        select(DiaryEntry).where(DiaryEntry.world_id == world_id)
    ):
        revision = session.scalar(
            select(DiaryRevision)
            .where(DiaryRevision.diary_entry_id == diary.id)
            .order_by(DiaryRevision.revision_number.desc())
        )
        assert revision is not None
        snapshot["memory"][diary.agent_id]["diaries"][diary.id] = {
            "simulation_date": diary.simulation_date,
            "content": diary.current_content,
            "mood": revision.mood,
            "location": revision.location,
            "revision_number": revision.revision_number,
        }
    for conversation in session.scalars(
        select(ConversationRecord).where(ConversationRecord.world_id == world_id)
    ):
        snapshot["memory"][conversation.owner_agent_id]["conversations"][conversation.id] = {
            "speaker_id": conversation.speaker_id,
            "target_agent_id": conversation.target_agent_id,
            "channel": conversation.channel,
            "content": conversation.content,
        }
    for relationship in session.scalars(
        select(Relationship).where(Relationship.world_id == world_id)
    ):
        snapshot["memory"][relationship.observer_agent_id]["relationships"][relationship.id] = {
            "target_agent_id": relationship.target_agent_id,
            "relationship_type": relationship.relationship_type,
            "rationale": relationship.rationale,
            "trust_score": relationship.trust_score,
            "affinity_score": relationship.affinity_score,
            "interaction_count": relationship.interaction_count,
        }
    for summary in session.scalars(
        select(MemorySummary).where(MemorySummary.world_id == world_id)
    ):
        source_ids = list(
            session.scalars(
                select(MemorySummarySource.memory_id)
                .where(MemorySummarySource.summary_id == summary.id)
                .order_by(MemorySummarySource.memory_id)
            ).all()
        )
        snapshot["memory"][summary.agent_id]["summaries"][summary.id] = {
            "algorithm": summary.algorithm,
            "content": summary.content,
            "source_memory_ids": source_ids,
        }
    return snapshot


def replay_snapshot(session: Session, world_id: str) -> dict[str, Any]:
    world = session.get(World, world_id)
    if world is None:
        raise ValueError("world not found")
    initial = world.config_json.get("initial_state")
    if not isinstance(initial, dict):
        raise ValueError("world does not contain a replayable initial_state")
    agents = session.scalars(
        select(Agent).where(Agent.world_id == world_id).order_by(Agent.id)
    ).all()
    snapshot: dict[str, Any] = {
        "simulation_time": str(initial["simulation_time"]),
        "pending_reactions": [],
        "pending_boosts": [],
        "agents": {
            agent.id: {
                "name": agent.name,
                "location": initial["location"],
                "mood": initial["mood"],
                "status": initial["status"],
                "is_alive": initial["is_alive"],
                "energy": initial["energy"],
                "knowledge": initial["knowledge"],
                "influence": initial["influence"],
                "credits": initial["credits"],
                "zero_energy_since": None,
            }
            for agent in agents
        },
        "memory": {agent.id: _empty_memory_state() for agent in agents},
    }
    events = session.scalars(
        select(WorldEvent)
        .where(WorldEvent.world_id == world_id)
        .order_by(WorldEvent.sequence_number)
    ).all()
    for event in events:
        apply_event(snapshot, event)
    return snapshot


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    names_by_id = {
        agent_id: agent["name"] for agent_id, agent in snapshot["agents"].items()
    }
    stable_snapshot = {
        "simulation_time": snapshot["simulation_time"],
        "agents": sorted(snapshot["agents"].values(), key=lambda agent: agent["name"]),
        "pending_boosts": [
            {
                "agent_name": boost.get("agent_name") or names_by_id[boost["agent_id"]],
                "queue_sequence": boost["queue_sequence"],
            }
            for boost in snapshot["pending_boosts"]
        ],
        "pending_reactions": [
            {
                "agent_name": reaction.get("agent_name")
                or names_by_id[reaction["agent_id"]],
                "queue_sequence": reaction["queue_sequence"],
            }
            for reaction in snapshot["pending_reactions"]
        ],
        "memory": {
            names_by_id[agent_id]: {
                kind: sorted(values.values(), key=lambda value: dumps(value, sort_keys=True))
                for kind, values in memory.items()
            }
            for agent_id, memory in snapshot.get("memory", {}).items()
        },
    }
    canonical = dumps(
        stable_snapshot, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _empty_memory_state() -> dict[str, dict[str, Any]]:
    return {
        "episodic": {},
        "soul": {},
        "diaries": {},
        "conversations": {},
        "relationships": {},
        "summaries": {},
    }
