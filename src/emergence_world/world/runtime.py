"""Deterministic headless world stepping."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from emergence_world.db.models import SimulationClock, Turn, World
from emergence_world.db.types import TurnStatus, TurnType
from emergence_world.mechanisms.economy import settle_pitch_cycles
from emergence_world.mechanisms.needs import apply_need_decay
from emergence_world.world.clock import advance_clock
from emergence_world.world.scheduler import next_agent


@dataclass(frozen=True, slots=True)
class StepResult:
    turn_id: str
    sequence_number: int
    agent_name: str
    turn_type: str
    simulation_time: str


def step_world(session: Session, world_id: str, minutes: int = 30) -> StepResult:
    world = session.get(World, world_id)
    clock = session.get(SimulationClock, world_id)
    if world is None or clock is None:
        raise ValueError("world not found")
    agent, turn_type = next_agent(session, world_id, clock.current_time)
    sequence = (
        session.scalar(
            select(func.max(Turn.sequence_number)).where(Turn.world_id == world_id)
        )
        or 0
    ) + 1
    turn = Turn(
        world_id=world_id,
        agent_id=agent.id,
        sequence_number=sequence,
        turn_type=turn_type,
        status=TurnStatus.COMPLETED,
        tool_call_budget=int(
            world.config_json["parameters"][
                "reaction_tool_limit"
                if turn_type == TurnType.REACTION
                else "turn_tool_limit"
            ]
        ),
        tool_calls_used=0,
        started_at=clock.current_time,
    )
    session.add(turn)
    advanced = advance_clock(session, world_id, minutes)
    turn.ended_at = advanced.current_time
    apply_need_decay(
        session,
        world_id=world_id,
        elapsed_minutes=minutes,
        simulation_time=advanced.current_time,
        parameters=world.config_json["parameters"],
    )
    settle_pitch_cycles(
        session,
        world_id=world_id,
        now=advanced.current_time,
        parameters=world.config_json["parameters"],
    )
    session.flush()
    return StepResult(
        turn_id=turn.id,
        sequence_number=sequence,
        agent_name=agent.name,
        turn_type=turn_type.value,
        simulation_time=advanced.current_time.isoformat(),
    )
