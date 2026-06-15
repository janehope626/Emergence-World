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
from emergence_world.agents.providers.recording import ProviderAuditMetadata
from emergence_world.agents.providers.security import redact_secrets
from emergence_world.agents.providers.smoke import ProviderFailure
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
        metadata = self._metadata()
        self._session.add(
            ProviderInteraction(
                world_id=self._turn.world_id,
                turn_id=self._turn.id,
                sequence_number=sequence,
                provider=self._provider.provider_name,
                model_name=self._provider.model_name,
                request_json=redact_secrets({
                    "context_hash": self._turn.context_hash,
                    "context": context.model_dump(mode="json"),
                    "tool_call_budget": tool_call_budget,
                    "prior_results": [
                        result.model_dump(mode="json") for result in prior_results
                    ],
                }),
                raw_response_json=redact_secrets(
                    metadata.raw_response
                    if metadata is not None
                    else decision.model_dump(mode="json")
                ),
                parsed_tool_calls_json=[
                    call.model_dump(mode="json") for call in decision.tool_calls
                ],
                parse_error=(
                    redact_secrets(metadata.parse_error) if metadata is not None else None
                ),
                input_tokens=metadata.input_tokens if metadata is not None else 0,
                output_tokens=metadata.output_tokens if metadata is not None else 0,
                total_tokens=metadata.total_tokens if metadata is not None else 0,
                latency_ms=(
                    metadata.latency_ms
                    if metadata is not None and metadata.latency_ms is not None
                    else latency_ms
                ),
                cost_usd=metadata.cost_usd if metadata is not None else 0,
            )
        )
        self._session.flush()

    def record_failure(
        self,
        *,
        context: AgentContext,
        tool_call_budget: int,
        prior_results: tuple[ToolExecutionResult, ...],
        failure: ProviderFailure,
        latency_ms: float,
    ) -> None:
        metadata = self._metadata() or ProviderAuditMetadata(
            raw_response=failure.raw_response,
            parse_error=f"{failure.code.value}: {failure}",
        )
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
                request_json=redact_secrets(
                    {
                        "context_hash": self._turn.context_hash,
                        "context": context.model_dump(mode="json"),
                        "tool_call_budget": tool_call_budget,
                        "prior_results": [
                            result.model_dump(mode="json") for result in prior_results
                        ],
                    }
                ),
                raw_response_json=redact_secrets(metadata.raw_response),
                parsed_tool_calls_json=[],
                parse_error=redact_secrets(
                    metadata.parse_error or f"{failure.code.value}: {failure}"
                ),
                input_tokens=metadata.input_tokens,
                output_tokens=metadata.output_tokens,
                total_tokens=metadata.total_tokens,
                latency_ms=metadata.latency_ms or latency_ms,
                cost_usd=metadata.cost_usd,
            )
        )
        self._session.flush()

    def _metadata(self) -> ProviderAuditMetadata | None:
        value = getattr(self._provider, "last_audit_metadata", None)
        return value if isinstance(value, ProviderAuditMetadata) else None
