"""Deterministic handlers for the first manual tool set."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from emergence_world.db.models import Agent, AgentState, Landmark


@dataclass(frozen=True, slots=True)
class PendingEvent:
    event_type: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class HandlerOutput:
    result: dict[str, Any]
    events: tuple[PendingEvent, ...] = ()


def list_agents(session: Session, world_id: str, arguments: dict[str, Any]) -> HandlerOutput:
    del arguments
    rows = session.execute(
        select(Agent, AgentState, Landmark)
        .join(AgentState, AgentState.agent_id == Agent.id)
        .outerjoin(Landmark, Landmark.id == AgentState.current_landmark_id)
        .where(Agent.world_id == world_id)
        .order_by(Agent.name)
    ).all()
    return HandlerOutput(
        {
            "agents": [
                {
                    "name": agent.name,
                    "alive": state.is_alive,
                    "status": state.status.value,
                    "location": landmark.name if landmark else None,
                }
                for agent, state, landmark in rows
            ]
        }
    )


def list_landmarks(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    del arguments
    landmarks = session.scalars(
        select(Landmark).where(Landmark.world_id == world_id).order_by(Landmark.name)
    ).all()
    return HandlerOutput(
        {
            "landmarks": [
                {
                    "name": landmark.name,
                    "category": landmark.category,
                    "description": landmark.description,
                    "is_open": landmark.is_open,
                }
                for landmark in landmarks
            ]
        }
    )


def inspect_location(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    agent_id = str(arguments.pop("_agent_id"))
    row = session.execute(
        select(AgentState, Landmark)
        .join(Landmark, Landmark.id == AgentState.current_landmark_id)
        .where(AgentState.world_id == world_id, AgentState.agent_id == agent_id)
    ).one()
    state, landmark = row
    return HandlerOutput(
        {
            "name": landmark.name,
            "category": landmark.category,
            "description": landmark.description,
            "is_open": landmark.is_open,
            "gated_tools": landmark.metadata_json.get("gated_tools", []),
            "agent_status": state.status.value,
        }
    )


def go_to_place(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    agent_id = str(arguments.pop("_agent_id"))
    destination = session.scalar(
        select(Landmark).where(
            Landmark.world_id == world_id, Landmark.name == arguments["place"]
        )
    )
    if destination is None:
        raise ValueError(f"unknown landmark: {arguments['place']}")
    if not destination.is_open:
        raise ValueError(f"landmark is closed: {destination.name}")
    state = session.get(AgentState, agent_id)
    if state is None:
        raise ValueError("agent state not found")
    previous = session.get(Landmark, state.current_landmark_id)
    if previous is not None and previous.id == destination.id:
        return HandlerOutput(
            {"from": previous.name, "to": destination.name, "moved": False}
        )
    state.current_landmark_id = destination.id
    return HandlerOutput(
        {
            "from": previous.name if previous else None,
            "to": destination.name,
            "moved": True,
        },
        (
            PendingEvent(
                "agent_moved",
                {
                    "agent_id": agent_id,
                    "from_landmark_id": previous.id if previous else None,
                    "from": previous.name if previous else None,
                    "to_landmark_id": destination.id,
                    "to": destination.name,
                },
            ),
        ),
    )


def idle(session: Session, world_id: str, arguments: dict[str, Any]) -> HandlerOutput:
    del session, world_id
    agent_id = str(arguments.pop("_agent_id"))
    duration = int(arguments.get("duration_minutes", 1))
    return HandlerOutput(
        {"duration_minutes": duration},
        (PendingEvent("agent_idled", {"agent_id": agent_id, "duration_minutes": duration}),),
    )


CORE_HANDLERS = {
    "list_agents": list_agents,
    "list_landmarks": list_landmarks,
    "inspect_location": inspect_location,
    "go_to_place": go_to_place,
    "idle": idle,
}
