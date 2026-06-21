"""Agent context, decision contracts, and provider-neutral runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from emergence_world.agents.context import AgentContextBuilder
from emergence_world.agents.models import AgentContext, AgentDecision, RequestedToolCall

if TYPE_CHECKING:
    from emergence_world.agents.runtime import AgentTurnRuntime

__all__ = [
    "AgentContext",
    "AgentContextBuilder",
    "AgentDecision",
    "AgentTurnRuntime",
    "RequestedToolCall",
]


def __getattr__(name: str) -> Any:
    if name == "AgentTurnRuntime":
        from emergence_world.agents.runtime import AgentTurnRuntime

        return AgentTurnRuntime
    raise AttributeError(name)
