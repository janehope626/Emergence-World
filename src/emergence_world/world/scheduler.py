"""Persistent round-robin and boost-turn scheduling."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from emergence_world.db.models import (
    Agent,
    AgentState,
    BoostTurnRequest,
    ReactionRequest,
    SimulationClock,
    Turn,
)
from emergence_world.db.types import TurnType
from emergence_world.world.events import append_system_event


def enqueue_boost(session: Session, world_id: str, agent_id: str) -> BoostTurnRequest:
    state = session.get(AgentState, agent_id)
    if state is None or state.world_id != world_id or not state.is_alive:
        raise ValueError("only live agents can receive boost turns")
    sequence = (
        session.scalar(
            select(func.max(BoostTurnRequest.sequence_number)).where(
                BoostTurnRequest.world_id == world_id
            )
        )
        or 0
    ) + 1
    request = BoostTurnRequest(
        world_id=world_id, agent_id=agent_id, sequence_number=sequence
    )
    session.add(request)
    session.flush()
    clock = session.get(SimulationClock, world_id)
    if clock is None:
        raise ValueError("simulation clock not found")
    append_system_event(
        session,
        world_id=world_id,
        system_rule="boost_queue",
        event_type="boost_enqueued",
        payload={"agent_id": agent_id, "queue_sequence": sequence},
        simulation_time=clock.current_time,
    )
    return request


def next_agent(
    session: Session, world_id: str, simulation_time: datetime
) -> tuple[Agent, TurnType]:
    reaction = session.scalar(
        select(ReactionRequest)
        .join(AgentState, AgentState.agent_id == ReactionRequest.agent_id)
        .where(
            ReactionRequest.world_id == world_id,
            ReactionRequest.consumed.is_(False),
            AgentState.is_alive.is_(True),
        )
        .order_by(ReactionRequest.sequence_number)
    )
    if reaction is not None:
        reaction.consumed = True
        reaction.consumed_at = simulation_time
        agent = session.get(Agent, reaction.agent_id)
        assert agent is not None
        append_system_event(
            session,
            world_id=world_id,
            system_rule="reaction_queue",
            event_type="reaction_consumed",
            payload={
                "agent_id": reaction.agent_id,
                "queue_sequence": reaction.sequence_number,
            },
            simulation_time=simulation_time,
        )
        return agent, TurnType.REACTION
    boost = session.scalar(
        select(BoostTurnRequest)
        .join(AgentState, AgentState.agent_id == BoostTurnRequest.agent_id)
        .where(
            BoostTurnRequest.world_id == world_id,
            BoostTurnRequest.consumed.is_(False),
            AgentState.is_alive.is_(True),
        )
        .order_by(BoostTurnRequest.sequence_number)
    )
    if boost is not None:
        boost.consumed = True
        boost.consumed_at = simulation_time
        agent = session.get(Agent, boost.agent_id)
        assert agent is not None
        append_system_event(
            session,
            world_id=world_id,
            system_rule="boost_queue",
            event_type="boost_consumed",
            payload={
                "agent_id": boost.agent_id,
                "queue_sequence": boost.sequence_number,
            },
            simulation_time=simulation_time,
        )
        return agent, TurnType.BOOST

    agents = session.scalars(
        select(Agent)
        .join(AgentState, AgentState.agent_id == Agent.id)
        .where(Agent.world_id == world_id, AgentState.is_alive.is_(True))
        .order_by(Agent.name)
    ).all()
    if not agents:
        raise ValueError("world has no live agents")
    last_regular = session.scalar(
        select(Turn)
        .where(Turn.world_id == world_id, Turn.turn_type == TurnType.REGULAR)
        .order_by(Turn.sequence_number.desc())
        .limit(1)
    )
    if last_regular is None:
        return agents[0], TurnType.REGULAR
    ids = [agent.id for agent in agents]
    try:
        index = ids.index(last_regular.agent_id or "")
    except ValueError:
        index = -1
    return agents[(index + 1) % len(agents)], TurnType.REGULAR
