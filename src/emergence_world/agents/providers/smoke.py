# 定义模型连通性检查配置以及可分类的提供方失败类型。
"""Provider smoke-test budgets and standardized failure boundaries."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ProviderSmokeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_turns: int = Field(default=20, ge=1)
    max_provider_calls_per_turn: int = Field(default=8, ge=1)
    max_tool_calls_per_turn: int = Field(default=30, ge=1)
    max_input_tokens_per_request: int = Field(default=32_000, ge=1)
    max_output_tokens_per_request: int = Field(default=2_000, ge=1)
    max_total_cost_usd: float = Field(default=1.0, ge=0)
    timeout_seconds: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=2, ge=0)


DEFAULT_PROVIDER_SMOKE_CONFIG = ProviderSmokeConfig()


class ProviderFailureCode(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    PROVIDER_ERROR = "provider_error"
    INVALID_JSON = "invalid_json"
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    DUPLICATE_CALL_ID = "duplicate_call_id"
    EMPTY_RESPONSE = "empty_response"
    BUDGET_EXCEEDED = "budget_exceeded"


class ProviderFailure(RuntimeError):
    def __init__(
        self,
        code: ProviderFailureCode,
        message: str,
        *,
        raw_response: object | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.raw_response = raw_response
