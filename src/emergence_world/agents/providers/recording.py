"""Offline provider that records and replays saved provider responses."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
import json
from typing import Never

from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate
from pydantic import ValidationError

from emergence_world.agents.models import AgentContext, AgentDecision, ToolExecutionResult
from emergence_world.agents.providers.smoke import (
    DEFAULT_PROVIDER_SMOKE_CONFIG,
    ProviderFailure,
    ProviderFailureCode,
    ProviderSmokeConfig,
)


@dataclass(frozen=True, slots=True)
class ProviderAuditMetadata:
    raw_response: object | None
    parse_error: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: float | None = None
    cost_usd: float | None = None


@dataclass(frozen=True, slots=True)
class RecordedProviderResponse:
    raw_response: object | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: float | None = None
    cost_usd: float | None = None


@dataclass(frozen=True, slots=True)
class RecordedProviderFailure:
    code: ProviderFailureCode
    message: str
    raw_response: object | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: float | None = None
    cost_usd: float | None = None


class RecordingProvider:
    provider_name = "recording"
    model_name = "offline-recording"

    def __init__(
        self,
        responses: Iterable[RecordedProviderResponse | RecordedProviderFailure | object],
        *,
        smoke_config: ProviderSmokeConfig = DEFAULT_PROVIDER_SMOKE_CONFIG,
    ) -> None:
        self._responses = deque(
            item
            if isinstance(item, RecordedProviderResponse | RecordedProviderFailure)
            else RecordedProviderResponse(item)
            for item in responses
        )
        self.smoke_config = smoke_config
        self.last_audit_metadata: ProviderAuditMetadata | None = None
        self.total_cost_usd = 0.0

    async def decide(
        self,
        context: AgentContext,
        tool_call_budget: int,
        prior_results: tuple[ToolExecutionResult, ...] = (),
    ) -> AgentDecision:
        del prior_results
        if not self._responses:
            self._fail(ProviderFailureCode.EMPTY_RESPONSE, "no recorded response remains")
        response = self._responses.popleft()
        if isinstance(response, RecordedProviderFailure):
            self.last_audit_metadata = ProviderAuditMetadata(
                raw_response=response.raw_response,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                total_tokens=response.total_tokens,
                latency_ms=response.latency_ms,
                cost_usd=response.cost_usd,
            )
            self._fail(response.code, response.message)
        raw = response.raw_response
        self.last_audit_metadata = ProviderAuditMetadata(
            raw_response=raw,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            total_tokens=response.total_tokens,
            latency_ms=response.latency_ms,
            cost_usd=response.cost_usd,
        )
        if raw is None or raw == "":
            self._fail(ProviderFailureCode.EMPTY_RESPONSE, "provider returned an empty response")
        if response.input_tokens and response.input_tokens > self.smoke_config.max_input_tokens_per_request:
            self._fail(ProviderFailureCode.BUDGET_EXCEEDED, "input token budget exceeded")
        if response.output_tokens and response.output_tokens > self.smoke_config.max_output_tokens_per_request:
            self._fail(ProviderFailureCode.BUDGET_EXCEEDED, "output token budget exceeded")
        prospective_cost = self.total_cost_usd + (response.cost_usd or 0.0)
        if prospective_cost > self.smoke_config.max_total_cost_usd:
            self._fail(ProviderFailureCode.BUDGET_EXCEEDED, "total cost budget exceeded")
        self.total_cost_usd = prospective_cost
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError as exc:
            self._fail(ProviderFailureCode.INVALID_JSON, f"invalid JSON: {exc.msg}")
        if not isinstance(payload, dict):
            self._fail(ProviderFailureCode.INVALID_JSON, "response must be a JSON object")
        try:
            decision = AgentDecision.model_validate(payload)
        except ValidationError as exc:
            self._fail(ProviderFailureCode.INVALID_JSON, f"invalid decision schema: {exc}")
        if len(decision.tool_calls) > min(
            tool_call_budget, self.smoke_config.max_tool_calls_per_turn
        ):
            self._fail(ProviderFailureCode.BUDGET_EXCEEDED, "tool-call budget exceeded")
        seen: set[str] = set()
        tools = {tool.name: tool for tool in context.available_tools}
        for call in decision.tool_calls:
            if call.call_id in seen:
                self._fail(
                    ProviderFailureCode.DUPLICATE_CALL_ID,
                    f"duplicate call_id: {call.call_id}",
                )
            seen.add(call.call_id)
            definition = tools.get(call.tool_name)
            if definition is None:
                self._fail(ProviderFailureCode.UNKNOWN_TOOL, f"unknown tool: {call.tool_name}")
            try:
                validate(instance=call.arguments, schema=definition.argument_schema)
            except JsonSchemaValidationError as exc:
                self._fail(
                    ProviderFailureCode.INVALID_ARGUMENTS,
                    f"invalid arguments for {call.tool_name}: {exc.message}",
                )
        return decision

    def _fail(self, code: ProviderFailureCode, message: str) -> Never:
        metadata = self.last_audit_metadata or ProviderAuditMetadata(raw_response=None)
        self.last_audit_metadata = ProviderAuditMetadata(
            raw_response=metadata.raw_response,
            parse_error=f"{code.value}: {message}",
            input_tokens=metadata.input_tokens,
            output_tokens=metadata.output_tokens,
            total_tokens=metadata.total_tokens,
            latency_ms=metadata.latency_ms,
            cost_usd=metadata.cost_usd,
        )
        raise ProviderFailure(code, message, raw_response=metadata.raw_response)
