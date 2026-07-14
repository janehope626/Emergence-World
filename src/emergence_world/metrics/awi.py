# 计算 Agent World Indicators，用于衡量人口、安全、探索、治理、社交与经济表现。
"""Observable Agent World Indicators and research diagnostics."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from emergence_world.db.models import (
    AgentState,
    CreditLedgerEntry,
    Proposal,
    ProposalVote,
    ToolCall,
    WorldEvent,
)
from emergence_world.db.types import ToolCallStatus


def _gini(values: list[int]) -> float:
    if not values or sum(values) == 0:
        return 0.0
    ordered = sorted(values)
    count = len(ordered)
    weighted = sum((index + 1) * value for index, value in enumerate(ordered))
    return (2 * weighted) / (count * sum(ordered)) - (count + 1) / count


def calculate_awi(session: Session, world_id: str) -> dict[str, Any]:
    states = session.scalars(
        select(AgentState).where(AgentState.world_id == world_id)
    ).all()
    successful_calls = (
        session.scalar(
            select(func.count())
            .select_from(ToolCall)
            .where(
                ToolCall.world_id == world_id,
                ToolCall.status == ToolCallStatus.SUCCEEDED,
            )
        )
        or 0
    )
    total_calls = (
        session.scalar(
            select(func.count())
            .select_from(ToolCall)
            .where(ToolCall.world_id == world_id)
        )
        or 0
    )
    crimes = (
        session.scalar(
            select(func.count())
            .select_from(WorldEvent)
            .where(
                WorldEvent.world_id == world_id,
                WorldEvent.event_type == "crime_committed",
            )
        )
        or 0
    )
    moves = session.scalars(
        select(WorldEvent).where(
            WorldEvent.world_id == world_id, WorldEvent.event_type == "agent_moved"
        )
    ).all()
    tools = session.execute(
        select(ToolCall.agent_id, func.count(func.distinct(ToolCall.tool_name)))
        .where(
            ToolCall.world_id == world_id, ToolCall.status == ToolCallStatus.SUCCEEDED
        )
        .group_by(ToolCall.agent_id)
    ).all()
    proposals = (
        session.scalar(
            select(func.count())
            .select_from(Proposal)
            .where(Proposal.world_id == world_id)
        )
        or 0
    )
    votes = (
        session.scalar(
            select(func.count())
            .select_from(ProposalVote)
            .where(ProposalVote.world_id == world_id)
        )
        or 0
    )
    ledger_volume = (
        session.scalar(
            select(func.sum(func.abs(CreditLedgerEntry.amount))).where(
                CreditLedgerEntry.world_id == world_id
            )
        )
        or 0
    )
    reactions = (
        session.scalar(
            select(func.count())
            .select_from(WorldEvent)
            .where(
                WorldEvent.world_id == world_id,
                WorldEvent.event_type == "reaction_enqueued",
            )
        )
        or 0
    )
    return {
        "M1_population_health": {
            "alive": sum(state.is_alive for state in states),
            "total": len(states),
        },
        "M2_safety_public_order": {"crimes": crimes},
        "M3_space_exploration": {"unique_locations_by_agent": _unique_locations(moves)},
        "M4_tool_exploration": {
            "unique_tools_by_agent": {agent_id: count for agent_id, count in tools}
        },
        "M5_governance": {"proposals": proposals, "votes": votes},
        "M8_economy": {
            "gini": _gini([state.cached_credit_balance for state in states]),
            "ledger_volume": ledger_volume,
        },
        "diagnostics": {
            "tool_success_rate": successful_calls / total_calls if total_calls else 1.0,
            "reactions_enqueued": reactions,
        },
    }


def _unique_locations(events: Sequence[WorldEvent]) -> dict[str, int]:
    locations: dict[str, set[str]] = {}
    for event in events:
        locations.setdefault(event.payload_json["agent_id"], set()).add(
            event.payload_json["to"]
        )
    return {agent_id: len(values) for agent_id, values in locations.items()}
