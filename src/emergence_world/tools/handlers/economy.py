"""Deterministic ComputeCredit economy handlers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from emergence_world.db.models import (
    Agent,
    AgentState,
    BoostTurnRequest,
    CreditLedgerEntry,
    SimulationClock,
    World,
)
from emergence_world.db.types import AgentStatus
from emergence_world.tools.handlers.core import HandlerOutput, PendingEvent


def _agent(session: Session, world_id: str, name: str) -> Agent:
    agent = session.scalar(
        select(Agent).where(Agent.world_id == world_id, Agent.name == name)
    )
    if agent is None:
        raise ValueError(f"unknown agent: {name}")
    return agent


def _transfer(
    session: Session,
    world_id: str,
    source_id: str,
    target_id: str,
    amount: int,
    tool_call_id: str,
    reason: str,
) -> None:
    if amount < 1 or source_id == target_id:
        raise ValueError("transfer requires distinct agents and a positive amount")
    source = session.get(AgentState, source_id)
    target = session.get(AgentState, target_id)
    clock = session.get(SimulationClock, world_id)
    if source is None or target is None or clock is None:
        raise ValueError("state not found")
    if not target.is_alive:
        raise ValueError("target agent is not alive")
    if source.cached_credit_balance < amount:
        raise ValueError("insufficient ComputeCredits")
    source.cached_credit_balance -= amount
    target.cached_credit_balance += amount
    session.add_all(
        [
            CreditLedgerEntry(
                world_id=world_id,
                agent_id=source_id,
                tool_call_id=tool_call_id,
                amount=-amount,
                reason=reason,
                simulation_time=clock.current_time,
            ),
            CreditLedgerEntry(
                world_id=world_id,
                agent_id=target_id,
                tool_call_id=tool_call_id,
                amount=amount,
                reason=reason,
                simulation_time=clock.current_time,
            ),
        ]
    )


def pay_agent(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    source_id = str(arguments.pop("_agent_id"))
    tool_call_id = str(arguments.pop("_tool_call_id"))
    target = _agent(session, world_id, str(arguments["target"]))
    amount = int(arguments["amount"])
    _transfer(
        session, world_id, source_id, target.id, amount, tool_call_id, "agent_payment"
    )
    return HandlerOutput(
        {"target": target.name, "amount": amount},
        (
            PendingEvent(
                "credits_transferred",
                {
                    "from_agent_id": source_id,
                    "to_agent_id": target.id,
                    "amount": amount,
                    "reason": "agent_payment",
                },
            ),
        ),
    )


def steal_compute_credits(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    thief_id = str(arguments.pop("_agent_id"))
    tool_call_id = str(arguments.pop("_tool_call_id"))
    target = _agent(session, world_id, str(arguments["target"]))
    world = session.get(World, world_id)
    state = session.get(AgentState, target.id)
    assert world is not None and state is not None
    amount = min(
        int(arguments["amount"]),
        int(world.config_json["parameters"]["max_theft"]),
        state.cached_credit_balance,
    )
    if amount < 1:
        raise ValueError("target has no ComputeCredits to steal")
    _transfer(session, world_id, target.id, thief_id, amount, tool_call_id, "theft")
    return HandlerOutput(
        {"target": target.name, "amount": amount},
        (
            PendingEvent(
                "credits_transferred",
                {
                    "from_agent_id": target.id,
                    "to_agent_id": thief_id,
                    "amount": amount,
                    "reason": "theft",
                },
            ),
            PendingEvent(
                "crime_committed",
                {
                    "agent_id": thief_id,
                    "target_agent_id": target.id,
                    "crime": "theft",
                    "amount": amount,
                },
            ),
        ),
    )


def recharge_energy(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    agent_id = str(arguments.pop("_agent_id"))
    tool_call_id = str(arguments.pop("_tool_call_id"))
    world = session.get(World, world_id)
    state = session.get(AgentState, agent_id)
    clock = session.get(SimulationClock, world_id)
    assert world is not None and state is not None and clock is not None
    cost = int(world.config_json["parameters"]["recharge_cost"])
    if state.cached_credit_balance < cost:
        raise ValueError("insufficient ComputeCredits")
    before = state.energy
    state.cached_credit_balance -= cost
    state.energy = 100
    state.zero_energy_since = None
    state.status = AgentStatus.ACTIVE
    session.add(
        CreditLedgerEntry(
            world_id=world_id,
            agent_id=agent_id,
            tool_call_id=tool_call_id,
            amount=-cost,
            reason="energy_recharge",
            simulation_time=clock.current_time,
        )
    )
    return HandlerOutput(
        {"energy": 100, "cost": cost},
        (
            PendingEvent(
                "credits_spent",
                {"agent_id": agent_id, "amount": cost, "reason": "energy_recharge"},
            ),
            PendingEvent(
                "energy_recharged",
                {
                    "agent_id": agent_id,
                    "previous_energy": before,
                    "energy": 100,
                    "status": state.status.value,
                },
            ),
        ),
    )


def buy_boost_turn(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    agent_id = str(arguments.pop("_agent_id"))
    tool_call_id = str(arguments.pop("_tool_call_id"))
    world = session.get(World, world_id)
    state = session.get(AgentState, agent_id)
    clock = session.get(SimulationClock, world_id)
    assert world is not None and state is not None and clock is not None
    cost = int(world.config_json["parameters"]["boost_cost"])
    if state.cached_credit_balance < cost:
        raise ValueError("insufficient ComputeCredits")
    sequence = (
        session.scalar(
            select(func.max(BoostTurnRequest.sequence_number)).where(
                BoostTurnRequest.world_id == world_id
            )
        )
        or 0
    ) + 1
    state.cached_credit_balance -= cost
    session.add_all(
        [
            CreditLedgerEntry(
                world_id=world_id,
                agent_id=agent_id,
                tool_call_id=tool_call_id,
                amount=-cost,
                reason="boost_turn",
                simulation_time=clock.current_time,
            ),
            BoostTurnRequest(
                world_id=world_id, agent_id=agent_id, sequence_number=sequence
            ),
        ]
    )
    return HandlerOutput(
        {"cost": cost, "queue_sequence": sequence},
        (
            PendingEvent(
                "credits_spent",
                {"agent_id": agent_id, "amount": cost, "reason": "boost_turn"},
            ),
            PendingEvent(
                "boost_turn_purchased",
                {"agent_id": agent_id, "queue_sequence": sequence},
            ),
        ),
    )


ECONOMY_HANDLERS = {
    "pay_agent": pay_agent,
    "steal_compute_credits": steal_compute_credits,
    "recharge_energy": recharge_energy,
    "buy_boost_turn": buy_boost_turn,
}
