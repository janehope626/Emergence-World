"""Private, deterministic, and event-audited memory tools."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from emergence_world.db.models import (
    Agent,
    AgentState,
    DiaryEntry,
    DiaryRevision,
    EpisodicMemory,
    Landmark,
    MemorySummary,
    MemorySummarySource,
    Relationship,
    RelationshipRevision,
    SimulationClock,
    SoulEntry,
    SoulEntryRevision,
)
from emergence_world.mechanisms.memory import (
    SELF_CARE_BATCH_SIZE,
    SELF_CARE_MINIMUM,
    SUMMARY_ALGORITHM,
    deterministic_summary_v1,
)
from emergence_world.tools.handlers.core import HandlerOutput, PendingEvent


def _identity(arguments: dict[str, Any]) -> tuple[str, str]:
    return str(arguments.pop("_agent_id")), str(arguments.pop("_tool_call_id"))


def _clock(session: Session, world_id: str) -> SimulationClock:
    clock = session.get(SimulationClock, world_id)
    if clock is None:
        raise ValueError("simulation clock not found")
    return clock


def _target(session: Session, world_id: str, name: str) -> Agent:
    agent = session.scalar(
        select(Agent).where(Agent.world_id == world_id, Agent.name == name)
    )
    if agent is None:
        raise ValueError(f"unknown agent: {name}")
    return agent


def add_to_longterm_memory(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    agent_id, tool_call_id = _identity(arguments)
    memory = EpisodicMemory(
        world_id=world_id,
        agent_id=agent_id,
        tool_call_id=tool_call_id,
        content=str(arguments["content"]),
        importance=float(arguments.get("importance", 0.5)),
        tags_json=sorted(set(str(tag) for tag in arguments.get("tags", []))),
    )
    session.add(memory)
    session.flush()
    return HandlerOutput(
        {"memory_id": memory.id},
        (
            PendingEvent(
                "memory_added",
                {
                    "memory_id": memory.id,
                    "agent_id": agent_id,
                    "content": memory.content,
                    "importance": memory.importance,
                    "tags": memory.tags_json,
                    "created_at": memory.created_at.isoformat(),
                },
            ),
        ),
    )


def retrieve_specific_memories(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    agent_id, _ = _identity(arguments)
    query = str(arguments["query"]).lower()
    limit = int(arguments.get("limit", 10))
    memories = session.scalars(
        select(EpisodicMemory)
        .where(
            EpisodicMemory.world_id == world_id,
            EpisodicMemory.agent_id == agent_id,
            EpisodicMemory.active.is_(True),
            func.lower(EpisodicMemory.content).contains(query),
        )
        .order_by(EpisodicMemory.importance.desc(), EpisodicMemory.created_at.desc())
        .limit(limit)
    ).all()
    return HandlerOutput(
        {
            "memories": [
                {
                    "id": memory.id,
                    "content": memory.content,
                    "importance": memory.importance,
                    "tags": memory.tags_json,
                }
                for memory in memories
            ]
        }
    )


def add_to_soul(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    agent_id, tool_call_id = _identity(arguments)
    entry = SoulEntry(
        world_id=world_id,
        agent_id=agent_id,
        current_content=str(arguments["content"]),
    )
    session.add(entry)
    session.flush()
    revision = SoulEntryRevision(
        world_id=world_id,
        soul_entry_id=entry.id,
        agent_id=agent_id,
        tool_call_id=tool_call_id,
        revision_number=1,
        content=entry.current_content,
    )
    session.add(revision)
    session.flush()
    return HandlerOutput(
        {"soul_entry_id": entry.id},
        (
            PendingEvent(
                "soul_entry_added",
                {
                    "soul_entry_id": entry.id,
                    "revision_id": revision.id,
                    "agent_id": agent_id,
                    "content": entry.current_content,
                    "created_at": revision.created_at.isoformat(),
                },
            ),
        ),
    )


def list_soul_entries(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    agent_id, _ = _identity(arguments)
    entries = session.scalars(
        select(SoulEntry)
        .where(
            SoulEntry.world_id == world_id,
            SoulEntry.agent_id == agent_id,
            SoulEntry.active.is_(True),
        )
        .order_by(SoulEntry.created_at, SoulEntry.id)
    ).all()
    return HandlerOutput(
        {"entries": [{"id": entry.id, "content": entry.current_content} for entry in entries]}
    )


def write_diary(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    agent_id, tool_call_id = _identity(arguments)
    clock = _clock(session, world_id)
    simulation_date = clock.current_time.date().isoformat()
    entry = session.scalar(
        select(DiaryEntry).where(
            DiaryEntry.world_id == world_id,
            DiaryEntry.agent_id == agent_id,
            DiaryEntry.simulation_date == simulation_date,
        )
    )
    event_type = "diary_written"
    if entry is None:
        entry = DiaryEntry(
            world_id=world_id,
            agent_id=agent_id,
            simulation_date=simulation_date,
            current_content=str(arguments["content"]),
        )
        session.add(entry)
        session.flush()
        revision_number = 1
    else:
        event_type = "diary_revised"
        entry.current_content = str(arguments["content"])
        revision_number = (
            session.scalar(
                select(func.max(DiaryRevision.revision_number)).where(
                    DiaryRevision.diary_entry_id == entry.id
                )
            )
            or 0
        ) + 1
    state = session.get(AgentState, agent_id)
    landmark = session.get(Landmark, state.current_landmark_id) if state else None
    revision = DiaryRevision(
        world_id=world_id,
        diary_entry_id=entry.id,
        agent_id=agent_id,
        tool_call_id=tool_call_id,
        revision_number=revision_number,
        content=entry.current_content,
        mood=str(arguments.get("mood", state.mood if state else "")) or None,
        location=str(arguments.get("location", landmark.name if landmark else "")) or None,
    )
    session.add(revision)
    session.flush()
    return HandlerOutput(
        {"diary_entry_id": entry.id, "revision_number": revision_number},
        (
            PendingEvent(
                event_type,
                {
                    "diary_entry_id": entry.id,
                    "revision_id": revision.id,
                    "agent_id": agent_id,
                    "simulation_date": simulation_date,
                    "revision_number": revision_number,
                    "content": revision.content,
                    "mood": revision.mood,
                    "location": revision.location,
                    "created_at": revision.created_at.isoformat(),
                },
            ),
        ),
    )


def read_diary(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    agent_id, _ = _identity(arguments)
    entries = session.scalars(
        select(DiaryEntry)
        .where(DiaryEntry.world_id == world_id, DiaryEntry.agent_id == agent_id)
        .order_by(DiaryEntry.simulation_date.desc())
        .limit(int(arguments.get("limit", 10)))
    ).all()
    return HandlerOutput(
        {
            "entries": [
                {
                    "id": entry.id,
                    "date": entry.simulation_date,
                    "content": entry.current_content,
                }
                for entry in entries
            ]
        }
    )


def assign_relationship(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    observer_id, tool_call_id = _identity(arguments)
    target = _target(session, world_id, str(arguments["target"]))
    if target.id == observer_id:
        raise ValueError("cannot assign a relationship to self")
    relationship = session.scalar(
        select(Relationship).where(
            Relationship.world_id == world_id,
            Relationship.observer_agent_id == observer_id,
            Relationship.target_agent_id == target.id,
        )
    )
    if relationship is None:
        relationship = Relationship(
            world_id=world_id,
            observer_agent_id=observer_id,
            target_agent_id=target.id,
            relationship_type=str(arguments["relationship_type"]),
            rationale=str(arguments["rationale"]),
        )
        session.add(relationship)
        session.flush()
        revision_number = 1
    else:
        revision_number = (
            session.scalar(
                select(func.max(RelationshipRevision.revision_number)).where(
                    RelationshipRevision.relationship_id == relationship.id
                )
            )
            or 0
        ) + 1
    relationship.relationship_type = str(arguments["relationship_type"])
    relationship.rationale = str(arguments["rationale"])
    relationship.trust_score = float(arguments.get("trust_score", relationship.trust_score))
    relationship.affinity_score = float(
        arguments.get("affinity_score", relationship.affinity_score)
    )
    relationship.interaction_count += 1
    revision = RelationshipRevision(
        world_id=world_id,
        relationship_id=relationship.id,
        observer_agent_id=observer_id,
        target_agent_id=target.id,
        tool_call_id=tool_call_id,
        revision_number=revision_number,
        relationship_type=relationship.relationship_type,
        rationale=relationship.rationale,
        trust_score=relationship.trust_score,
        affinity_score=relationship.affinity_score,
        interaction_count=relationship.interaction_count,
    )
    session.add(revision)
    session.flush()
    return HandlerOutput(
        {"relationship_id": relationship.id, "revision_number": revision_number},
        (
            PendingEvent(
                "relationship_assigned",
                {
                    "relationship_id": relationship.id,
                    "revision_id": revision.id,
                    "observer_agent_id": observer_id,
                    "target_agent_id": target.id,
                    "relationship_type": relationship.relationship_type,
                    "rationale": relationship.rationale,
                    "trust_score": relationship.trust_score,
                    "affinity_score": relationship.affinity_score,
                    "interaction_count": relationship.interaction_count,
                    "created_at": revision.created_at.isoformat(),
                },
            ),
        ),
    )


def list_relationships(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    observer_id, _ = _identity(arguments)
    rows = session.execute(
        select(Relationship, Agent)
        .join(Agent, Agent.id == Relationship.target_agent_id)
        .where(
            Relationship.world_id == world_id,
            Relationship.observer_agent_id == observer_id,
        )
        .order_by(Agent.name)
    ).all()
    return HandlerOutput(
        {
            "relationships": [
                {
                    "target": target.name,
                    "relationship_type": relationship.relationship_type,
                    "rationale": relationship.rationale,
                    "trust_score": relationship.trust_score,
                    "affinity_score": relationship.affinity_score,
                }
                for relationship, target in rows
            ]
        }
    )


def self_care(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    agent_id, tool_call_id = _identity(arguments)
    agent = session.get(Agent, agent_id)
    state = session.get(AgentState, agent_id)
    if agent is None or state is None or state.current_landmark_id != agent.home_landmark_id:
        raise ValueError("self_care must be performed at the agent's assigned home")
    memories = list(
        session.scalars(
            select(EpisodicMemory)
            .where(
                EpisodicMemory.world_id == world_id,
                EpisodicMemory.agent_id == agent_id,
                EpisodicMemory.active.is_(True),
            )
            .order_by(EpisodicMemory.created_at, EpisodicMemory.id)
            .limit(SELF_CARE_BATCH_SIZE)
        ).all()
    )
    if len(memories) < SELF_CARE_MINIMUM:
        raise ValueError(f"self_care requires at least {SELF_CARE_MINIMUM} active memories")
    summary = MemorySummary(
        world_id=world_id,
        agent_id=agent_id,
        tool_call_id=tool_call_id,
        algorithm=SUMMARY_ALGORITHM,
        content=deterministic_summary_v1(memories),
        source_count=len(memories),
    )
    session.add(summary)
    session.flush()
    for memory in memories:
        memory.active = False
        memory.archived_at = _clock(session, world_id).current_time
        session.add(MemorySummarySource(summary_id=summary.id, memory_id=memory.id))
    source_ids = sorted(memory.id for memory in memories)
    return HandlerOutput(
        {"summary_id": summary.id, "source_count": len(memories)},
        (
            PendingEvent(
                "memory_summarized",
                {
                    "summary_id": summary.id,
                    "agent_id": agent_id,
                    "algorithm": summary.algorithm,
                    "content": summary.content,
                    "source_memory_ids": source_ids,
                    "created_at": summary.created_at.isoformat(),
                },
            ),
            PendingEvent(
                "memory_archived",
                {
                    "agent_id": agent_id,
                    "memory_ids": source_ids,
                    "archived_at": _clock(session, world_id).current_time.isoformat(),
                },
            ),
        ),
    )


MEMORY_HANDLERS = {
    "add_to_longterm_memory": add_to_longterm_memory,
    "retrieve_specific_memories": retrieve_specific_memories,
    "add_to_soul": add_to_soul,
    "list_soul_entries": list_soul_entries,
    "write_diary": write_diary,
    "read_diary": read_diary,
    "assign_relationship": assign_relationship,
    "list_relationships": list_relationships,
    "self_care": self_care,
}
