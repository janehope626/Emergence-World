# 实现积分发放、资助周期创建与结算等经济规则。
"""ComputeCredit ledger rules and Victory Arch settlement."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from emergence_world.db.models import (
    AgentState,
    CreditLedgerEntry,
    Pitch,
    PitchCycle,
    PitchVote,
)
from emergence_world.world.events import append_system_event


def grant_credits(
    session: Session,
    *,
    world_id: str,
    agent_id: str,
    amount: int,
    reason: str,
    simulation_time: datetime,
) -> None:
    if amount < 1:
        raise ValueError("grant amount must be positive")
    state = session.get(AgentState, agent_id)
    if state is None or state.world_id != world_id:
        raise ValueError("agent state not found")
    state.cached_credit_balance += amount
    session.add(
        CreditLedgerEntry(
            world_id=world_id,
            agent_id=agent_id,
            system_rule=reason,
            amount=amount,
            reason=reason,
            simulation_time=simulation_time,
        )
    )
    append_system_event(
        session,
        world_id=world_id,
        system_rule=reason,
        event_type="credits_granted",
        payload={"agent_id": agent_id, "amount": amount, "reason": reason},
        simulation_time=simulation_time,
    )


def ensure_pitch_cycle(session: Session, world_id: str, now: datetime) -> PitchCycle:
    cycle = session.scalar(
        select(PitchCycle)
        .where(PitchCycle.world_id == world_id, PitchCycle.settled.is_(False))
        .order_by(PitchCycle.sequence_number.desc())
    )
    if cycle is not None:
        return cycle
    sequence = (
        session.scalar(
            select(func.max(PitchCycle.sequence_number)).where(
                PitchCycle.world_id == world_id
            )
        )
        or 0
    ) + 1
    cycle = PitchCycle(
        world_id=world_id,
        sequence_number=sequence,
        starts_at=now,
        ends_at=now + timedelta(days=2),
    )
    session.add(cycle)
    session.flush()
    return cycle


def settle_pitch_cycles(
    session: Session, *, world_id: str, now: datetime, parameters: dict[str, Any]
) -> None:
    cycles = session.scalars(
        select(PitchCycle).where(
            PitchCycle.world_id == world_id,
            PitchCycle.settled.is_(False),
            PitchCycle.ends_at <= now,
        )
    ).all()
    rewards = [int(value) for value in parameters["pitch_rewards"]]
    for cycle in cycles:
        pitches = session.scalars(select(Pitch).where(Pitch.cycle_id == cycle.id)).all()
        ranked = sorted(
            pitches,
            key=lambda pitch: (
                -(
                    session.scalar(
                        select(func.count())
                        .select_from(PitchVote)
                        .where(PitchVote.pitch_id == pitch.id)
                    )
                    or 0
                ),
                pitch.created_at,
                pitch.agent_id,
            ),
        )
        for pitch, reward in zip(ranked, rewards, strict=False):
            grant_credits(
                session,
                world_id=world_id,
                agent_id=pitch.agent_id,
                amount=reward,
                reason="pitch_reward",
                simulation_time=now,
            )
        cycle.settled = True
        append_system_event(
            session,
            world_id=world_id,
            system_rule="pitch_cycle",
            event_type="pitch_cycle_settled",
            payload={
                "cycle_id": cycle.id,
                "winner_agent_ids": [
                    pitch.agent_id for pitch in ranked[: len(rewards)]
                ],
            },
            simulation_time=now,
        )
        ensure_pitch_cycle(session, world_id, now)
