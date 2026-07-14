# 按世界时间推进智能体需求衰减，并处理能量耗尽状态。
"""Deterministic need decay and survival rules."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from emergence_world.db.models import AgentState
from emergence_world.db.types import AgentStatus
from emergence_world.world.events import append_system_event


def apply_need_decay(
    session: Session,
    *,
    world_id: str,
    elapsed_minutes: int,
    simulation_time: datetime,
    parameters: dict[str, Any],
) -> None:
    if elapsed_minutes < 1:
        raise ValueError("elapsed_minutes must be at least 1")
    periods = {
        "energy": float(parameters["energy_decay_hours"]) * 60,
        "knowledge": float(parameters["knowledge_decay_hours"]) * 60,
        "influence": float(parameters["influence_decay_hours"]) * 60,
    }
    death_delay = timedelta(hours=float(parameters["death_after_zero_energy_hours"]))
    states = session.scalars(
        select(AgentState)
        .where(AgentState.world_id == world_id, AgentState.is_alive.is_(True))
        .order_by(AgentState.agent_id)
    ).all()
    for state in states:
        previous = (state.energy, state.knowledge, state.influence, state.status)
        interval_start = simulation_time - timedelta(minutes=elapsed_minutes)
        minutes_until_zero = state.energy * periods["energy"] / 100
        state.energy = max(
            0.0, state.energy - 100 * elapsed_minutes / periods["energy"]
        )
        state.knowledge = max(
            0.0, state.knowledge - 100 * elapsed_minutes / periods["knowledge"]
        )
        state.influence = max(
            0.0, state.influence - 100 * elapsed_minutes / periods["influence"]
        )
        if state.energy == 0:
            state.zero_energy_since = state.zero_energy_since or (
                interval_start + timedelta(minutes=minutes_until_zero)
            )
            state.status = AgentStatus.CRITICAL
        else:
            state.zero_energy_since = None
            state.status = AgentStatus.ACTIVE
        append_system_event(
            session,
            world_id=world_id,
            system_rule="need_decay",
            event_type="needs_changed",
            payload={
                "agent_id": state.agent_id,
                "previous": {
                    "energy": previous[0],
                    "knowledge": previous[1],
                    "influence": previous[2],
                    "status": previous[3].value,
                },
                "energy": state.energy,
                "knowledge": state.knowledge,
                "influence": state.influence,
                "zero_energy_since": (
                    state.zero_energy_since.isoformat()
                    if state.zero_energy_since is not None
                    else None
                ),
                "status": state.status.value,
            },
            simulation_time=simulation_time,
        )
        if (
            state.zero_energy_since is not None
            and simulation_time - state.zero_energy_since >= death_delay
        ):
            state.is_alive = False
            state.status = AgentStatus.DEAD
            append_system_event(
                session,
                world_id=world_id,
                system_rule="energy_death",
                event_type="agent_died",
                payload={"agent_id": state.agent_id, "cause": "energy_depletion"},
                simulation_time=simulation_time,
            )
