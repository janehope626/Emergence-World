# 通过 OpenAI 兼容接口调用豆包模型，并把响应规范化为内部智能体决策。
"""Doubao/Volcengine Ark OpenAI-compatible chat provider."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
import os
from typing import Any, Protocol, cast

from openai import APIError, APITimeoutError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel, ConfigDict, Field

from emergence_world.agents.models import AgentContext, AgentDecision, ToolExecutionResult
from emergence_world.agents.prompts import load_agent_turn_prompt
from emergence_world.agents.providers.recording import (
    ProviderAuditMetadata,
    RecordedProviderResponse,
    RecordingProvider,
)
from emergence_world.agents.providers.smoke import (
    ProviderFailure,
    ProviderFailureCode,
    ProviderSmokeConfig,
)


DEFAULT_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


class DoubaoProviderConfig(BaseModel):
    """Non-secret Doubao provider configuration persisted in run manifests."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    model: str = Field(min_length=1)
    smoke_config: ProviderSmokeConfig
    input_cost_per_million_tokens_usd: float = Field(gt=0)
    output_cost_per_million_tokens_usd: float = Field(gt=0)
    base_url: str = DEFAULT_ARK_BASE_URL
    credential_env_var: str = Field(
        default="ARK_API_KEY", min_length=1, validation_alias="api_key_env"
    )

    def manifest_parameters(self) -> dict[str, Any]:
        return {
            "api": "openai_compatible_chat_completions",
            **self.model_dump(mode="json", exclude={"model"}),
        }


class ChatCompletionsAPI(Protocol):
    def create(self, **kwargs: Any) -> Awaitable[object]: ...


class ChatAPI(Protocol):
    completions: ChatCompletionsAPI


class OpenAICompatibleClient(Protocol):
    chat: ChatAPI


class DoubaoProvider:
    """Translate Ark chat-completion tool calls into audited agent decisions."""

    provider_name = "doubao"

    def __init__(
        self,
        config: DoubaoProviderConfig,
        *,
        client: OpenAICompatibleClient | None = None,
        api_key: str | Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        self.config = config
        self.model_name = config.model
        self.smoke_config = config.smoke_config
        resolved_key = (
            api_key if api_key is not None else os.environ.get(config.credential_env_var)
        )
        if client is None and resolved_key is None:
            raise ValueError(
                f"{config.credential_env_var} is required for the doubao provider"
            )
        self._client = client or cast(
            OpenAICompatibleClient,
            AsyncOpenAI(
                api_key=resolved_key,
                base_url=config.base_url,
                timeout=config.smoke_config.timeout_seconds,
                max_retries=config.smoke_config.max_retries,
            ),
        )
        self.last_audit_metadata: ProviderAuditMetadata | None = None
        self.total_cost_usd = 0.0

    def manifest_parameters(self) -> dict[str, Any]:
        return self.config.manifest_parameters()

    async def decide(
        self,
        context: AgentContext,
        tool_call_budget: int,
        prior_results: tuple[ToolExecutionResult, ...] = (),
    ) -> AgentDecision:
        request = self._request(context, tool_call_budget, prior_results)
        try:
            response = await self._client.chat.completions.create(**request)
        except APITimeoutError as exc:
            self._fail(ProviderFailureCode.TIMEOUT, str(exc))
        except RateLimitError as exc:
            self._fail(ProviderFailureCode.RATE_LIMITED, str(exc))
        except APIError as exc:
            self._fail(ProviderFailureCode.PROVIDER_ERROR, str(exc))
        except Exception as exc:
            self._fail(ProviderFailureCode.PROVIDER_ERROR, str(exc))

        try:
            raw = _model_dump(response)
        except ProviderFailure as exc:
            self.last_audit_metadata = ProviderAuditMetadata(
                raw_response=response,
                parse_error=f"{exc.code.value}: {exc}",
            )
            raise

        usage_value = raw.get("usage")
        usage: dict[str, Any] = usage_value if isinstance(usage_value, dict) else {}
        input_tokens = _optional_int(usage.get("prompt_tokens"))
        output_tokens = _optional_int(usage.get("completion_tokens"))
        total_tokens = _optional_int(usage.get("total_tokens"))
        cost = self._cost(input_tokens, output_tokens)

        try:
            decision_payload = _decision_payload(raw)
        except ProviderFailure as exc:
            self.last_audit_metadata = ProviderAuditMetadata(
                raw_response=raw,
                parse_error=f"{exc.code.value}: {exc}",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                cost_usd=cost,
            )
            raise ProviderFailure(exc.code, str(exc), raw_response=raw) from exc

        parser = RecordingProvider(
            [
                RecordedProviderResponse(
                    raw_response=decision_payload,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    cost_usd=cost,
                )
            ],
            smoke_config=self.smoke_config.model_copy(
                update={
                    "max_total_cost_usd": self.smoke_config.max_total_cost_usd
                    - self.total_cost_usd
                }
            ),
        )
        try:
            decision = await parser.decide(context, tool_call_budget, prior_results)
        except ProviderFailure as exc:
            parse_error = (
                parser.last_audit_metadata.parse_error
                if parser.last_audit_metadata is not None
                else f"{exc.code.value}: {exc}"
            )
            self.last_audit_metadata = ProviderAuditMetadata(
                raw_response=raw,
                parse_error=parse_error,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                cost_usd=cost,
            )
            raise ProviderFailure(exc.code, str(exc), raw_response=raw) from exc

        self.total_cost_usd += cost
        self.last_audit_metadata = ProviderAuditMetadata(
            raw_response=raw,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
        )
        return decision

    def _request(
        self,
        context: AgentContext,
        tool_call_budget: int,
        prior_results: tuple[ToolExecutionResult, ...],
    ) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": load_agent_turn_prompt()},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "context": context.model_dump(mode="json"),
                            "prior_tool_results": [
                                result.model_dump(mode="json") for result in prior_results
                            ],
                            "remaining_tool_call_budget": min(
                                tool_call_budget,
                                self.smoke_config.max_tool_calls_per_turn,
                            ),
                        },
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                },
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.argument_schema,
                    },
                }
                for tool in context.available_tools
            ],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "max_tokens": self.smoke_config.max_output_tokens_per_request,
        }

    def _cost(self, input_tokens: int | None, output_tokens: int | None) -> float:
        return (
            (input_tokens or 0) * self.config.input_cost_per_million_tokens_usd
            + (output_tokens or 0) * self.config.output_cost_per_million_tokens_usd
        ) / 1_000_000

    def _fail(self, code: ProviderFailureCode, message: str) -> None:
        self.last_audit_metadata = ProviderAuditMetadata(
            raw_response=None,
            parse_error=f"{code.value}: {message}",
        )
        raise ProviderFailure(code, message)


def _model_dump(response: object) -> dict[str, Any]:
    model_dump = getattr(response, "model_dump", None)
    raw = model_dump(mode="json") if callable(model_dump) else response
    if not isinstance(raw, dict):
        raise ProviderFailure(
            ProviderFailureCode.INVALID_JSON, "provider response must be an object"
        )
    return raw


def _decision_payload(raw: dict[str, Any]) -> dict[str, Any]:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderFailure(
            ProviderFailureCode.INVALID_JSON, "chat response choices must be a list"
        )
    first = choices[0]
    if not isinstance(first, dict):
        raise ProviderFailure(
            ProviderFailureCode.INVALID_JSON, "chat response choice must be an object"
        )
    message = first.get("message")
    if not isinstance(message, dict):
        raise ProviderFailure(
            ProviderFailureCode.INVALID_JSON, "chat response message must be an object"
        )

    calls: list[dict[str, Any]] = []
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for item in tool_calls:
            if not isinstance(item, dict):
                continue
            function = item.get("function")
            if not isinstance(function, dict):
                continue
            calls.append(
                {
                    "call_id": item.get("id"),
                    "tool_name": function.get("name"),
                    "arguments": _arguments(function.get("arguments")),
                }
            )

    content = message.get("content")
    reasoning = content if isinstance(content, str) and content else None
    if not calls and reasoning is None:
        raise ProviderFailure(
            ProviderFailureCode.EMPTY_RESPONSE, "provider returned no calls or message"
        )
    return {
        "tool_calls": calls,
        "reasoning_text": reasoning,
        "terminate": not calls,
    }


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _arguments(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ProviderFailure(
                ProviderFailureCode.INVALID_JSON,
                f"function arguments are invalid JSON: {exc.msg}",
            ) from exc
    return value
