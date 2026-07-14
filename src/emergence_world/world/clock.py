# 推进模拟时钟并保证世界时间单调递增。
"""Deterministic simulated-world clock."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from emergence_world.db.models import SimulationClock
from emergence_world.world.events import append_system_event


def advance_clock(session: Session, world_id: str, minutes: int) -> SimulationClock:
    if minutes < 1:
        raise ValueError("minutes must be at least 1")
    clock = session.get(SimulationClock, world_id)
    if clock is None:
        raise ValueError("simulation clock not found")
    previous = clock.current_time
    clock.current_time = previous + timedelta(minutes=minutes)
    clock.last_advanced_at = clock.current_time
    append_system_event(
        session,
        world_id=world_id,
        system_rule="simulation_clock",
        event_type="clock_advanced",
        payload={
            "from": previous.isoformat(),
            "to": clock.current_time.isoformat(),
            "minutes": minutes,
        },
        simulation_time=clock.current_time,
    )
    return clock
