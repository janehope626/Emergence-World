"""Provider-neutral agent turn runtime."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Literal, Protocol

from emergence_world.agents.models import (
    AgentContext,
    AgentTurnResult,
    RequestedToolCall,
    ToolExecutionResult,
)
from emergence_world.agents.providers.base import AgentProvider


class ToolExecutor(Protocol):
    """Narrow boundary through which the runtime may request state changes."""

    async def execute(
        self, *, agent_id: str, tool_call: RequestedToolCall
    ) -> ToolExecutionResult:
        """Validate and transactionally execute one requested tool call."""


class ProviderAuditor(Protocol):
    def record(
        self,
        *,
        context: AgentContext,
        tool_call_budget: int,
        prior_results: tuple[ToolExecutionResult, ...],
        decision: object,
        latency_ms: float,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class AgentTurnRuntime:
    provider: AgentProvider
    tool_executor: ToolExecutor
    provider_auditor: ProviderAuditor | None = None

    async def run(self, context: AgentContext, tool_call_budget: int) -> AgentTurnResult:
        if tool_call_budget < 1:
            raise ValueError("tool_call_budget must be at least 1")

        tool_results: list[ToolExecutionResult] = []
        reasoning_log: list[str] = []
        reason: Literal["provider_done", "max_tool_calls_reached"]

        while len(tool_results) < tool_call_budget:
            remaining = tool_call_budget - len(tool_results)
            started = monotonic()
            decision = await self.provider.decide(
                context, remaining, tuple(tool_results)
            )
            if self.provider_auditor is not None:
                self.provider_auditor.record(
                    context=context,
                    tool_call_budget=remaining,
                    prior_results=tuple(tool_results),
                    decision=decision,
                    latency_ms=(monotonic() - started) * 1000,
                )
            if decision.reasoning_text:
                reasoning_log.append(decision.reasoning_text)

            for tool_call in decision.tool_calls[:remaining]:
                result = await self.tool_executor.execute(
                    agent_id=context.agent_id, tool_call=tool_call
                )
                tool_results.append(result)

            if decision.terminate:
                reason = "provider_done"
                break
            if len(tool_results) >= tool_call_budget:
                reason = "max_tool_calls_reached"
                break
            if not decision.tool_calls:
                reason = "provider_done"
                break

        return AgentTurnResult(
            agent_id=context.agent_id,
            tool_results=tuple(tool_results),
            reasoning_log=tuple(reasoning_log),
            calls_used=len(tool_results),
            termination_reason=reason,
        )
