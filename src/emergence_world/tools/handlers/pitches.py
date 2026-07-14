# 实现资助提案的提交、浏览和投票工具。
"""Victory Arch pitch-cycle handlers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from emergence_world.db.models import Pitch, PitchVote, SimulationClock
from emergence_world.mechanisms.economy import ensure_pitch_cycle
from emergence_world.tools.handlers.core import HandlerOutput, PendingEvent


def submit_grant_pitch(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    agent_id = str(arguments.pop("_agent_id"))
    arguments.pop("_tool_call_id")
    clock = session.get(SimulationClock, world_id)
    assert clock is not None
    cycle = ensure_pitch_cycle(session, world_id, clock.current_time)
    pitch = Pitch(
        world_id=world_id,
        cycle_id=cycle.id,
        agent_id=agent_id,
        title=str(arguments["title"]),
        evidence=str(arguments["evidence"]),
    )
    session.add(pitch)
    session.flush()
    return HandlerOutput(
        {"pitch_id": pitch.id, "cycle_id": cycle.id},
        (
            PendingEvent(
                "pitch_submitted",
                {"agent_id": agent_id, "pitch_id": pitch.id, "cycle_id": cycle.id},
            ),
        ),
    )


def list_credit_pitches(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    arguments.pop("_agent_id")
    arguments.pop("_tool_call_id")
    clock = session.get(SimulationClock, world_id)
    assert clock is not None
    cycle = ensure_pitch_cycle(session, world_id, clock.current_time)
    pitches = session.scalars(
        select(Pitch).where(Pitch.cycle_id == cycle.id).order_by(Pitch.created_at)
    ).all()
    return HandlerOutput(
        {
            "cycle_id": cycle.id,
            "pitches": [
                {"id": pitch.id, "title": pitch.title, "agent_id": pitch.agent_id}
                for pitch in pitches
            ],
        }
    )


def vote_for_pitch(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    voter_id = str(arguments.pop("_agent_id"))
    arguments.pop("_tool_call_id")
    pitch = session.get(Pitch, str(arguments["pitch_id"]))
    if pitch is None or pitch.world_id != world_id:
        raise ValueError("pitch not found")
    if pitch.agent_id == voter_id:
        raise ValueError("agents cannot vote for their own pitch")
    session.add(
        PitchVote(
            world_id=world_id,
            cycle_id=pitch.cycle_id,
            pitch_id=pitch.id,
            voter_id=voter_id,
        )
    )
    return HandlerOutput(
        {"pitch_id": pitch.id},
        (
            PendingEvent(
                "pitch_vote_cast",
                {
                    "agent_id": voter_id,
                    "pitch_id": pitch.id,
                    "cycle_id": pitch.cycle_id,
                },
            ),
        ),
    )


PITCH_HANDLERS = {
    "submit_grant_pitch": submit_grant_pitch,
    "list_credit_pitches": list_credit_pitches,
    "vote_for_pitch": vote_for_pitch,
}
