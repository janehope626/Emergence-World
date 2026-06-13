"""Deterministic provider for tests and reproducible manual scenarios."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from emergence_world.agents.models import (
    AgentContext,
    AgentDecision,
    ToolExecutionResult,
)


class ScriptedProvider:
    provider_name = "scripted"
    model_name = "deterministic-sequence"

    def __init__(self, decisions: Iterable[AgentDecision]) -> None:
        self._decisions = deque(decisions)

    async def decide(
        self,
        context: AgentContext,
        tool_call_budget: int,
        prior_results: tuple[ToolExecutionResult, ...] = (),
    ) -> AgentDecision:
        del context, tool_call_budget, prior_results
        if not self._decisions:
            return AgentDecision(terminate=True)
        return self._decisions.popleft()
