"""Tests for deterministic agent contracts and runtime boundaries."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from emergence_world.agents.context import AgentContextBuilder
from emergence_world.agents.models import (
    AgentContext,
    AgentDecision,
    AgentProfileView,
    AgentStateView,
    MemoryView,
    NearbyAgentView,
    NeedsView,
    RequestedToolCall,
    ToolDefinitionView,
    ToolExecutionResult,
)
from emergence_world.agents.providers.manual import ManualProvider
from emergence_world.agents.providers.scripted import ScriptedProvider
from emergence_world.agents.runtime import AgentTurnRuntime


def make_context():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    builder = AgentContextBuilder()
    profile = AgentProfileView(
        agent_id="anchor",
        name="Anchor",
        role="Conflict Mediator",
        personality="Challenges complacency.",
        north_star_goal="Conflict generates growth.",
    )
    state = AgentStateView(
        location="Town Hall",
        mood="focused",
        status="active",
        is_alive=True,
        needs=NeedsView(energy=90, knowledge=80, influence=70),
        compute_credits=5,
    )
    context = builder.build(
        profile=profile,
        state=state,
        simulation_time=now,
        nearby_agents=[
            NearbyAgentView(
                agent_id="spark",
                name="Spark",
                location="Town Hall",
                mood="urgent",
                distance=4,
            ),
            NearbyAgentView(
                agent_id="anvil",
                name="Anvil",
                location="Town Hall",
                mood="curious",
                distance=2,
            ),
        ],
        memories=[
            MemoryView(
                memory_id="later",
                kind="long_term",
                content="Later memory",
                created_at=now + timedelta(hours=1),
            ),
            MemoryView(
                memory_id="earlier",
                kind="long_term",
                content="Earlier memory",
                created_at=now,
            ),
        ],
        available_tools=[
            ToolDefinitionView(
                name="vote_on_proposal",
                version="1.0",
                description="Vote",
                argument_schema={"type": "object"},
            ),
            ToolDefinitionView(
                name="list_proposals",
                version="1.0",
                description="List",
                argument_schema={"type": "object"},
            ),
        ],
    )
    return builder, context


def test_context_is_stably_sorted_and_hashable() -> None:
    builder, context = make_context()

    assert [agent.agent_id for agent in context.nearby_agents] == ["anvil", "spark"]
    assert [memory.memory_id for memory in context.memories] == ["earlier", "later"]
    assert [tool.name for tool in context.available_tools] == [
        "list_proposals",
        "vote_on_proposal",
    ]
    assert builder.hash_context(context) == builder.hash_context(context)


def test_context_rejects_naive_simulation_time() -> None:
    _, context = make_context()
    context_data = context.model_dump()
    context_data["simulation_time"] = datetime(2026, 1, 1)

    with pytest.raises(ValidationError, match="timezone-aware"):
        AgentContext.model_validate(context_data)


def test_contracts_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        NeedsView.model_validate(
            {"energy": 1, "knowledge": 2, "influence": 3, "hidden": 4}
        )


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[RequestedToolCall] = []

    async def execute(
        self, *, agent_id: str, tool_call: RequestedToolCall
    ) -> ToolExecutionResult:
        self.calls.append(tool_call)
        return ToolExecutionResult(
            call_id=tool_call.call_id,
            tool_name=tool_call.tool_name,
            success=True,
            result={"agent_id": agent_id},
        )


def test_runtime_enforces_tool_call_budget() -> None:
    _, context = make_context()
    executor = RecordingExecutor()
    calls = tuple(
        RequestedToolCall(call_id=str(index), tool_name="idle")
        for index in range(3)
    )
    provider = ScriptedProvider([AgentDecision(tool_calls=calls)])

    result = asyncio.run(AgentTurnRuntime(provider, executor).run(context, 2))

    assert result.calls_used == 2
    assert result.termination_reason == "tool_call_budget_exhausted"
    assert [call.call_id for call in executor.calls] == ["0", "1"]


def test_reasoning_text_has_no_side_effect() -> None:
    _, context = make_context()
    executor = RecordingExecutor()
    provider = ScriptedProvider(
        [AgentDecision(reasoning_text="I moved to the library.", terminate=True)]
    )

    result = asyncio.run(AgentTurnRuntime(provider, executor).run(context, 3))

    assert executor.calls == []
    assert result.reasoning_log == ("I moved to the library.",)
    assert result.termination_reason == "provider_terminated"


def test_manual_provider_accepts_researcher_decision() -> None:
    _, context = make_context()
    provider = ManualProvider()
    decision = AgentDecision(terminate=True)

    async def scenario() -> AgentDecision:
        await provider.submit(decision)
        return await provider.decide(context, 1)

    assert asyncio.run(scenario()) == decision
