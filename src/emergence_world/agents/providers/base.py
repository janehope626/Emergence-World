"""Provider-neutral agent decision interface."""

from __future__ import annotations

from typing import Protocol

from emergence_world.agents.models import (
    AgentContext,
    AgentDecision,
    ToolExecutionResult,
)


class AgentProvider(Protocol):
    """A decision source that cannot mutate world state directly."""

    provider_name: str
    model_name: str

    async def decide(
        self,
        context: AgentContext,
        tool_call_budget: int,
        prior_results: tuple[ToolExecutionResult, ...] = (),
    ) -> AgentDecision:
        """Choose structured tool calls from the supplied read-only context."""

