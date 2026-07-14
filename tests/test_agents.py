# 覆盖智能体上下文构建、模型提供方、回合运行时与审计行为。
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
from emergence_world.agents.providers.doubao import DoubaoProvider, DoubaoProviderConfig
from emergence_world.agents.providers.openai import OpenAIProvider, OpenAIProviderConfig
from emergence_world.agents.providers.recording import (
    RecordedProviderFailure,
    RecordedProviderResponse,
    RecordingProvider,
)
from emergence_world.agents.providers.scripted import ScriptedProvider
from emergence_world.agents.providers.smoke import ProviderFailure, ProviderFailureCode
from emergence_world.agents.providers.smoke import ProviderSmokeConfig
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
                argument_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
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
    assert result.termination_reason == "max_tool_calls_reached"
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
    assert result.termination_reason == "provider_done"


def test_manual_provider_accepts_researcher_decision() -> None:
    _, context = make_context()
    provider = ManualProvider()
    decision = AgentDecision(terminate=True)

    async def scenario() -> AgentDecision:
        await provider.submit(decision)
        return await provider.decide(context, 1)

    assert asyncio.run(scenario()) == decision


def test_recording_provider_replays_saved_response_and_metadata() -> None:
    _, context = make_context()
    raw = {
        "tool_calls": [
            {
                "call_id": "recorded-1",
                "tool_name": "list_proposals",
                "arguments": {},
            }
        ],
        "terminate": True,
    }
    provider = RecordingProvider(
        [
            RecordedProviderResponse(
                raw_response=raw,
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
                latency_ms=12.5,
                cost_usd=0.01,
            )
        ]
    )

    decision = asyncio.run(provider.decide(context, 3))

    assert decision.tool_calls[0].tool_name == "list_proposals"
    assert provider.last_audit_metadata is not None
    assert provider.last_audit_metadata.raw_response == raw
    assert provider.last_audit_metadata.total_tokens == 120


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("", ProviderFailureCode.EMPTY_RESPONSE),
        ("not-json", ProviderFailureCode.INVALID_JSON),
        (
            {
                "tool_calls": [
                    {"call_id": "1", "tool_name": "unknown", "arguments": {}}
                ]
            },
            ProviderFailureCode.UNKNOWN_TOOL,
        ),
        (
            {
                "tool_calls": [
                    {
                        "call_id": "1",
                        "tool_name": "list_proposals",
                        "arguments": {"unexpected": True},
                    }
                ]
            },
            ProviderFailureCode.INVALID_ARGUMENTS,
        ),
        (
            {
                "tool_calls": [
                    {"call_id": "1", "tool_name": "list_proposals", "arguments": {}},
                    {"call_id": "1", "tool_name": "list_proposals", "arguments": {}},
                ]
            },
            ProviderFailureCode.DUPLICATE_CALL_ID,
        ),
    ],
)
def test_recording_provider_classifies_parse_failures(
    raw: object, code: ProviderFailureCode
) -> None:
    _, context = make_context()
    provider = RecordingProvider([raw])

    with pytest.raises(ProviderFailure) as captured:
        asyncio.run(provider.decide(context, 3))

    assert captured.value.code == code
    assert provider.last_audit_metadata is not None
    assert provider.last_audit_metadata.parse_error.startswith(code.value)


@pytest.mark.parametrize(
    "code",
    [
        ProviderFailureCode.TIMEOUT,
        ProviderFailureCode.RATE_LIMITED,
        ProviderFailureCode.PROVIDER_ERROR,
        ProviderFailureCode.BUDGET_EXCEEDED,
    ],
)
def test_recording_provider_replays_operational_failures(
    code: ProviderFailureCode,
) -> None:
    _, context = make_context()
    provider = RecordingProvider([RecordedProviderFailure(code, "offline failure")])

    with pytest.raises(ProviderFailure) as captured:
        asyncio.run(provider.decide(context, 3))

    assert captured.value.code == code
    assert provider.last_audit_metadata is not None
    assert provider.last_audit_metadata.parse_error == f"{code.value}: offline failure"


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return self.payload


class FakeResponsesAPI:
    def __init__(
        self, response: FakeResponse | None = None, failure: Exception | None = None
    ) -> None:
        self.response = response
        self.failure = failure
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.requests.append(kwargs)
        if self.failure is not None:
            raise self.failure
        assert self.response is not None
        return self.response


class FakeOpenAIClient:
    def __init__(self, responses: FakeResponsesAPI) -> None:
        self.responses = responses


def openai_config(**updates: object) -> OpenAIProviderConfig:
    config: dict[str, object] = {
        "model": "mock-model",
        "smoke_config": ProviderSmokeConfig(
            max_turns=1,
            max_provider_calls_per_turn=2,
            max_tool_calls_per_turn=1,
            max_input_tokens_per_request=1_000,
            max_output_tokens_per_request=100,
            max_total_cost_usd=0.01,
            timeout_seconds=1,
            max_retries=0,
        ),
        "input_cost_per_million_tokens_usd": 1.0,
        "output_cost_per_million_tokens_usd": 2.0,
    }
    config.update(updates)
    return OpenAIProviderConfig.model_validate(config)


class FakeChatCompletionsAPI:
    def __init__(
        self, response: FakeResponse | None = None, failure: Exception | None = None
    ) -> None:
        self.response = response
        self.failure = failure
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.requests.append(kwargs)
        if self.failure is not None:
            raise self.failure
        assert self.response is not None
        return self.response


class FakeChatAPI:
    def __init__(self, completions: FakeChatCompletionsAPI) -> None:
        self.completions = completions


class FakeDoubaoClient:
    def __init__(self, completions: FakeChatCompletionsAPI) -> None:
        self.chat = FakeChatAPI(completions)


def doubao_config(**updates: object) -> DoubaoProviderConfig:
    config: dict[str, object] = {
        "model": "doubao-mock-endpoint",
        "smoke_config": ProviderSmokeConfig(
            max_turns=1,
            max_provider_calls_per_turn=2,
            max_tool_calls_per_turn=1,
            max_input_tokens_per_request=1_000,
            max_output_tokens_per_request=100,
            max_total_cost_usd=0.01,
            timeout_seconds=1,
            max_retries=0,
        ),
        "input_cost_per_million_tokens_usd": 1.0,
        "output_cost_per_million_tokens_usd": 2.0,
    }
    config.update(updates)
    return DoubaoProviderConfig.model_validate(config)


def test_openai_provider_builds_structured_request_and_parses_function_call(
    monkeypatch,
) -> None:
    secret = "openai-request-secret"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    _, context = make_context()
    responses = FakeResponsesAPI(
        FakeResponse(
            {
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "list_proposals",
                        "arguments": "{}",
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                },
            }
        )
    )
    provider = OpenAIProvider(openai_config(), client=FakeOpenAIClient(responses))

    decision = asyncio.run(provider.decide(context, 1))

    assert decision.tool_calls[0].tool_name == "list_proposals"
    assert responses.requests[0]["model"] == "mock-model"
    assert responses.requests[0]["parallel_tool_calls"] is False
    assert secret not in str(responses.requests[0])
    assert provider.last_audit_metadata is not None
    assert provider.last_audit_metadata.total_tokens == 120
    assert provider.last_audit_metadata.cost_usd == pytest.approx(0.00014)


def test_openai_provider_natural_language_cannot_change_state() -> None:
    _, context = make_context()
    responses = FakeResponsesAPI(
        FakeResponse(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "I moved to Town Hall.",
                            }
                        ],
                    }
                ],
                "usage": {},
            }
        )
    )
    provider = OpenAIProvider(openai_config(), client=FakeOpenAIClient(responses))

    decision = asyncio.run(provider.decide(context, 1))

    assert decision.tool_calls == ()
    assert decision.reasoning_text == "I moved to Town Hall."
    assert decision.terminate is True


def test_openai_provider_enforces_cost_budget_and_classifies_client_failure() -> None:
    _, context = make_context()
    costly = FakeResponsesAPI(
        FakeResponse(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Done."}],
                    }
                ],
                "usage": {"input_tokens": 1_000, "output_tokens": 100},
            }
        )
    )
    provider = OpenAIProvider(
        openai_config(
            smoke_config=ProviderSmokeConfig(
                max_turns=1,
                max_provider_calls_per_turn=1,
                max_tool_calls_per_turn=1,
                max_input_tokens_per_request=2_000,
                max_output_tokens_per_request=200,
                max_total_cost_usd=0.0001,
                timeout_seconds=1,
                max_retries=0,
            )
        ),
        client=FakeOpenAIClient(costly),
    )
    with pytest.raises(ProviderFailure) as captured:
        asyncio.run(provider.decide(context, 1))
    assert captured.value.code == ProviderFailureCode.BUDGET_EXCEEDED

    failed = OpenAIProvider(
        openai_config(),
        client=FakeOpenAIClient(FakeResponsesAPI(failure=RuntimeError("offline"))),
    )
    with pytest.raises(ProviderFailure) as captured:
        asyncio.run(failed.decide(context, 1))
    assert captured.value.code == ProviderFailureCode.PROVIDER_ERROR


def test_doubao_provider_builds_chat_request_and_parses_tool_call() -> None:
    _, context = make_context()
    completions = FakeChatCompletionsAPI(
        FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "list_proposals",
                                        "arguments": "{}",
                                    },
                                }
                            ]
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                },
            }
        )
    )
    provider = DoubaoProvider(
        doubao_config(), client=FakeDoubaoClient(completions)
    )

    decision = asyncio.run(provider.decide(context, 1))

    assert decision.tool_calls[0].tool_name == "list_proposals"
    assert completions.requests[0]["model"] == "doubao-mock-endpoint"
    assert completions.requests[0]["parallel_tool_calls"] is False
    tools = completions.requests[0]["tools"]
    assert isinstance(tools, list)
    assert tools[0]["type"] == "function"
    assert "function" in tools[0]
    assert provider.last_audit_metadata is not None
    assert provider.last_audit_metadata.total_tokens == 120
    assert provider.last_audit_metadata.cost_usd == pytest.approx(0.00014)


def test_doubao_provider_natural_language_cannot_change_state() -> None:
    _, context = make_context()
    completions = FakeChatCompletionsAPI(
        FakeResponse(
            {
                "choices": [
                    {"message": {"content": "I moved to Town Hall.", "tool_calls": []}}
                ],
                "usage": {},
            }
        )
    )
    provider = DoubaoProvider(doubao_config(), client=FakeDoubaoClient(completions))

    decision = asyncio.run(provider.decide(context, 1))

    assert decision.tool_calls == ()
    assert decision.reasoning_text == "I moved to Town Hall."
    assert decision.terminate is True


def test_doubao_provider_enforces_cost_budget_and_requires_api_key() -> None:
    _, context = make_context()
    costly = FakeChatCompletionsAPI(
        FakeResponse(
            {
                "choices": [{"message": {"content": "Done."}}],
                "usage": {"prompt_tokens": 1_000, "completion_tokens": 100},
            }
        )
    )
    provider = DoubaoProvider(
        doubao_config(
            smoke_config=ProviderSmokeConfig(
                max_turns=1,
                max_provider_calls_per_turn=1,
                max_tool_calls_per_turn=1,
                max_input_tokens_per_request=2_000,
                max_output_tokens_per_request=200,
                max_total_cost_usd=0.0001,
                timeout_seconds=1,
                max_retries=0,
            )
        ),
        client=FakeDoubaoClient(costly),
    )
    with pytest.raises(ProviderFailure) as captured:
        asyncio.run(provider.decide(context, 1))
    assert captured.value.code == ProviderFailureCode.BUDGET_EXCEEDED

    with pytest.raises(ValueError, match="ARK_API_KEY"):
        DoubaoProvider(doubao_config(api_key_env="ARK_API_KEY_DOES_NOT_EXIST"))
