"""Deterministic assembly of read-only agent context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Iterable

from emergence_world.agents.models import (
    AgentContext,
    AgentProfileView,
    AgentStateView,
    ConstitutionArticleView,
    MemoryView,
    NearbyAgentView,
    RelationshipView,
    ToolDefinitionView,
)


@dataclass(frozen=True, slots=True)
class AgentContextBuilder:
    """Build stable context without database or provider dependencies."""

    context_version: str = "1.0"

    def build(
        self,
        *,
        profile: AgentProfileView,
        state: AgentStateView,
        simulation_time: datetime,
        nearby_agents: Iterable[NearbyAgentView] = (),
        memories: Iterable[MemoryView] = (),
        relationships: Iterable[RelationshipView] = (),
        constitution: Iterable[ConstitutionArticleView] = (),
        available_tools: Iterable[ToolDefinitionView] = (),
    ) -> AgentContext:
        return AgentContext(
            context_version=self.context_version,
            agent_id=profile.agent_id,
            profile=profile,
            state=state,
            simulation_time=simulation_time,
            nearby_agents=tuple(
                sorted(nearby_agents, key=lambda item: (item.distance, item.agent_id))
            ),
            memories=tuple(
                sorted(memories, key=lambda item: (item.created_at, item.memory_id))
            ),
            relationships=tuple(
                sorted(relationships, key=lambda item: item.target_agent_id)
            ),
            constitution=tuple(
                sorted(constitution, key=lambda item: (item.position, item.article_id))
            ),
            available_tools=tuple(
                sorted(available_tools, key=lambda item: (item.name, item.version))
            ),
        )

    @staticmethod
    def hash_context(context: AgentContext) -> str:
        return sha256(context.canonical_json().encode("utf-8")).hexdigest()
