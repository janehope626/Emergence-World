# 提供由人工直接输入决策的模型提供方实现。
"""Manual decision provider for CLI-driven mechanism validation."""

from __future__ import annotations

from asyncio import Queue

from emergence_world.agents.models import (
    AgentContext,
    AgentDecision,
    ToolExecutionResult,
)


class ManualProvider:
    """Wait for a researcher or CLI layer to submit the next decision."""

    provider_name = "manual"
    model_name = "researcher"

    def __init__(self) -> None:
        self._decisions: Queue[AgentDecision] = Queue()

    async def submit(self, decision: AgentDecision) -> None:
        await self._decisions.put(decision)

    async def decide(
        self,
        context: AgentContext,
        tool_call_budget: int,
        prior_results: tuple[ToolExecutionResult, ...] = (),
    ) -> AgentDecision:
        del context, tool_call_budget, prior_results
        return await self._decisions.get()
