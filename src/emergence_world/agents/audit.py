"""Persistence audit for provider requests and structured decisions."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from emergence_world.agents.models import (
    AgentContext,
    AgentDecision,
    ToolExecutionResult,
)
from emergence_world.agents.providers.base import AgentProvider
from emergence_world.db.models import ProviderInteraction, Turn


class ProviderAuditRecorder:
    def __init__(self, session: Session, turn: Turn, provider: AgentProvider) -> None:
        self._session = session
        self._turn = turn
        self._provider = provider

    def record(
        self,
        *,
        context: AgentContext,
        tool_call_budget: int,
        prior_results: tuple[ToolExecutionResult, ...],
        decision: object,
        latency_ms: float,
    ) -> None:
        if not isinstance(decision, AgentDecision):
            raise TypeError("provider must return AgentDecision")
        sequence = (
            self._session.scalar(
                select(func.max(ProviderInteraction.sequence_number)).where(
                    ProviderInteraction.turn_id == self._turn.id
                )
            )
            or 0
        ) + 1
        self._session.add(
            ProviderInteraction(
                world_id=self._turn.world_id,
                turn_id=self._turn.id,
                sequence_number=sequence,
                provider=self._provider.provider_name,
                model_name=self._provider.model_name,
                request_json={
                    "context_hash": self._turn.context_hash,
                    "context": context.model_dump(mode="json"),
                    "tool_call_budget": tool_call_budget,
                    "prior_results": [
                        result.model_dump(mode="json") for result in prior_results
                    ],
                },
                raw_response_json=decision.model_dump(mode="json"),
                parsed_tool_calls_json=[
                    call.model_dump(mode="json") for call in decision.tool_calls
                ],
                parse_error=None,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                latency_ms=latency_ms,
                cost_usd=0,
            )
        )
        self._session.flush()
