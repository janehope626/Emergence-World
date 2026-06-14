"""Read-only contracts shared by agent context, providers, and runtime."""

from __future__ import annotations

from datetime import datetime
from json import dumps
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    """Strict immutable base for data crossing the agent-runtime boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentProfileView(ContractModel):
    agent_id: str
    name: str
    role: str
    personality: str
    north_star_goal: str


class NeedsView(ContractModel):
    energy: float = Field(ge=0, le=100)
    knowledge: float = Field(ge=0, le=100)
    influence: float = Field(ge=0, le=100)


class AgentStateView(ContractModel):
    location: str
    mood: str
    status: str
    is_alive: bool
    needs: NeedsView
    compute_credits: int = Field(ge=0)


class NearbyAgentView(ContractModel):
    agent_id: str
    name: str
    location: str
    mood: str
    distance: float = Field(ge=0)


class MemoryView(ContractModel):
    memory_id: str
    kind: Literal["long_term", "summary", "soul", "diary", "conversation"]
    content: str
    created_at: datetime


class RelationshipView(ContractModel):
    target_agent_id: str
    target_name: str
    relationship_type: str
    rationale: str
    interaction_count: int = Field(ge=0)


class ConstitutionArticleView(ContractModel):
    article_id: str
    position: int = Field(ge=1)
    title: str
    content: str


class ToolDefinitionView(ContractModel):
    name: str
    version: str
    description: str
    argument_schema: dict[str, Any]


class RecentEventView(ContractModel):
    event_id: str
    event_type: str
    payload: dict[str, Any]
    simulation_time: datetime


class AgentContext(ContractModel):
    """Complete read-only information visible to an agent for one decision."""

    context_version: str
    agent_id: str
    profile: AgentProfileView
    state: AgentStateView
    nearby_agents: tuple[NearbyAgentView, ...] = ()
    memories: tuple[MemoryView, ...] = ()
    relationships: tuple[RelationshipView, ...] = ()
    constitution: tuple[ConstitutionArticleView, ...] = ()
    available_tools: tuple[ToolDefinitionView, ...] = ()
    recent_events: tuple[RecentEventView, ...] = ()
    simulation_time: datetime

    @model_validator(mode="after")
    def validate_identity_and_time(self) -> AgentContext:
        if self.agent_id != self.profile.agent_id:
            raise ValueError("agent_id must match profile.agent_id")
        if self.simulation_time.tzinfo is None:
            raise ValueError("simulation_time must be timezone-aware")
        return self

    def canonical_json(self) -> str:
        """Return stable serialized context suitable for auditing and hashing."""

        return dumps(
            self.model_dump(mode="json", exclude_none=True),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


class RequestedToolCall(ContractModel):
    call_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionResult(ContractModel):
    call_id: str
    tool_name: str
    success: bool
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

    @model_validator(mode="after")
    def validate_error_state(self) -> ToolExecutionResult:
        if self.success and self.error is not None:
            raise ValueError("successful tool result cannot include an error")
        if not self.success and not self.error:
            raise ValueError("failed tool result must include an error")
        return self


class AgentDecision(ContractModel):
    tool_calls: tuple[RequestedToolCall, ...] = ()
    reasoning_text: str | None = None
    terminate: bool = False


class AgentTurnResult(ContractModel):
    agent_id: str
    tool_results: tuple[ToolExecutionResult, ...]
    reasoning_log: tuple[str, ...]
    calls_used: int = Field(ge=0)
    termination_reason: Literal[
        "provider_done",
        "max_tool_calls_reached",
    ]
