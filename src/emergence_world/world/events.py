"""Append-only world-event creation and replay reducers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from emergence_world.db.models import WorldEvent


@dataclass(frozen=True, slots=True)
class PendingSystemEvent:
    event_type: str
    payload: dict[str, Any]


def append_system_event(
    session: Session,
    *,
    world_id: str,
    system_rule: str,
    event_type: str,
    payload: dict[str, Any],
    simulation_time: datetime,
    turn_id: str | None = None,
) -> WorldEvent:
    event = WorldEvent(
        world_id=world_id,
        turn_id=turn_id,
        system_rule=system_rule,
        sequence_number=next_event_sequence(session, world_id),
        event_type=event_type,
        payload_json=payload,
        simulation_time=simulation_time,
    )
    session.add(event)
    session.flush()
    return event


def next_event_sequence(session: Session, world_id: str) -> int:
    return (
        session.scalar(
            select(func.max(WorldEvent.sequence_number)).where(
                WorldEvent.world_id == world_id
            )
        )
        or 0
    ) + 1


def apply_event(snapshot: dict[str, Any], event: WorldEvent) -> None:
    payload = event.payload_json
    memory = snapshot.get("memory")
    if memory is not None:
        owner_id = payload.get("agent_id") or payload.get("observer_agent_id")
        owner = memory.get(owner_id)
        if owner is not None:
            if event.event_type == "memory_added":
                owner["episodic"][payload["memory_id"]] = {
                    "content": payload["content"],
                    "importance": payload["importance"],
                    "tags": payload["tags"],
                    "active": True,
                    "archived_at": None,
                }
                return
            if event.event_type == "memory_archived":
                for memory_id in payload["memory_ids"]:
                    item = owner["episodic"][memory_id]
                    item["active"] = False
                    item["archived_at"] = payload["archived_at"]
                return
            if event.event_type == "soul_entry_added":
                owner["soul"][payload["soul_entry_id"]] = {
                    "content": payload["content"],
                    "active": True,
                }
                return
            if event.event_type in {"diary_written", "diary_revised"}:
                owner["diaries"][payload["diary_entry_id"]] = {
                    "simulation_date": payload["simulation_date"],
                    "content": payload["content"],
                    "mood": payload["mood"],
                    "location": payload["location"],
                    "revision_number": payload["revision_number"],
                }
                return
            if event.event_type == "conversation_recorded":
                owner["conversations"][payload["conversation_id"]] = {
                    "speaker_id": payload["speaker_id"],
                    "target_agent_id": payload["target_agent_id"],
                    "channel": payload["channel"],
                    "content": payload["content"],
                }
                return
            if event.event_type == "relationship_assigned":
                owner["relationships"][payload["relationship_id"]] = {
                    "target_agent_id": payload["target_agent_id"],
                    "relationship_type": payload["relationship_type"],
                    "rationale": payload["rationale"],
                    "trust_score": payload["trust_score"],
                    "affinity_score": payload["affinity_score"],
                    "interaction_count": payload["interaction_count"],
                }
                return
            if event.event_type == "memory_summarized":
                owner["summaries"][payload["summary_id"]] = {
                    "algorithm": payload["algorithm"],
                    "content": payload["content"],
                    "source_memory_ids": payload["source_memory_ids"],
                }
                return
    if event.event_type == "clock_advanced":
        snapshot["simulation_time"] = payload["to"]
        return
    if event.event_type in {"boost_enqueued", "boost_turn_purchased"}:
        snapshot["pending_boosts"].append(
            {
                "agent_id": payload["agent_id"],
                "queue_sequence": payload["queue_sequence"],
            }
        )
        return
    if event.event_type == "reaction_enqueued":
        snapshot["pending_reactions"].append(
            {
                "agent_id": payload["agent_id"],
                "queue_sequence": payload["queue_sequence"],
            }
        )
        return
    if event.event_type == "reaction_consumed":
        snapshot["pending_reactions"] = [
            item
            for item in snapshot["pending_reactions"]
            if item["queue_sequence"] != payload["queue_sequence"]
        ]
        return
    if event.event_type == "credits_transferred":
        snapshot["agents"][payload["from_agent_id"]]["credits"] -= payload["amount"]
        snapshot["agents"][payload["to_agent_id"]]["credits"] += payload["amount"]
        return
    if event.event_type == "credits_spent":
        snapshot["agents"][payload["agent_id"]]["credits"] -= payload["amount"]
        return
    if event.event_type == "credits_granted":
        snapshot["agents"][payload["agent_id"]]["credits"] += payload["amount"]
        return
    if event.event_type == "proposal_resolved":
        consequence = payload.get("consequence")
        if consequence and consequence.get("type") == "remove_agent":
            agent = snapshot["agents"][consequence["agent_id"]]
            agent["is_alive"] = False
            agent["status"] = "removed"
        return
    if event.event_type == "boost_consumed":
        snapshot["pending_boosts"] = [
            boost
            for boost in snapshot["pending_boosts"]
            if boost["queue_sequence"] != payload["queue_sequence"]
        ]
        return
    agent_id = payload.get("agent_id")
    if agent_id is None or agent_id not in snapshot["agents"]:
        return
    agent = snapshot["agents"][agent_id]
    if event.event_type == "agent_moved":
        agent["location"] = payload["to"]
    elif event.event_type == "needs_changed":
        agent["energy"] = payload["energy"]
        agent["knowledge"] = payload["knowledge"]
        agent["influence"] = payload["influence"]
        agent["zero_energy_since"] = payload["zero_energy_since"]
        agent["status"] = payload["status"]
    elif event.event_type == "agent_died":
        agent["is_alive"] = False
        agent["status"] = "dead"
    elif event.event_type == "energy_recharged":
        agent["energy"] = payload["energy"]
        agent["zero_energy_since"] = None
        agent["status"] = payload["status"]
