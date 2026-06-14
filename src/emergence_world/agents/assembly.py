"""Database-backed assembly of complete autonomous turn contexts."""

from __future__ import annotations

from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from emergence_world.agents.context import AgentContextBuilder
from emergence_world.agents.memory_context import build_memory_context
from emergence_world.agents.models import (
    AgentContext,
    AgentProfileView,
    AgentStateView,
    ConstitutionArticleView,
    MemoryView,
    NearbyAgentView,
    NeedsView,
    RecentEventView,
    RelationshipView,
    ToolDefinitionView,
)
from emergence_world.db.models import (
    Agent,
    AgentState,
    ConstitutionArticle,
    Landmark,
    Relationship,
    SimulationClock,
    ToolDefinition,
    World,
    WorldEvent,
)

AUTONOMOUS_CONTEXT_VERSION = "autonomous_context_v1"


def assemble_autonomous_context(
    session: Session, *, world_id: str, agent_id: str
) -> AgentContext:
    row = session.execute(
        select(Agent, AgentState, Landmark)
        .join(AgentState, AgentState.agent_id == Agent.id)
        .join(Landmark, Landmark.id == AgentState.current_landmark_id)
        .where(Agent.id == agent_id, Agent.world_id == world_id)
    ).one_or_none()
    clock = session.get(SimulationClock, world_id)
    world = session.get(World, world_id)
    if row is None or clock is None or world is None:
        raise ValueError("agent context source not found")
    agent, state, location = row
    timezone = ZoneInfo(str(world.config_json["parameters"]["timezone"]))
    context_time = clock.current_time.replace(tzinfo=timezone)

    memory_context = build_memory_context(
        session, world_id=world_id, agent_id=agent_id
    ).context
    memories = _memory_views(memory_context, context_time)
    nearby = session.execute(
        select(Agent, AgentState, Landmark)
        .join(AgentState, AgentState.agent_id == Agent.id)
        .join(Landmark, Landmark.id == AgentState.current_landmark_id)
        .where(
            Agent.world_id == world_id,
            Agent.id != agent_id,
            AgentState.is_alive.is_(True),
            AgentState.current_landmark_id == state.current_landmark_id,
        )
        .order_by(Agent.name)
    ).all()
    relationships = session.execute(
        select(Relationship, Agent)
        .join(Agent, Agent.id == Relationship.target_agent_id)
        .where(
            Relationship.world_id == world_id,
            Relationship.observer_agent_id == agent_id,
        )
        .order_by(Agent.name)
    ).all()
    constitution = session.scalars(
        select(ConstitutionArticle)
        .where(
            ConstitutionArticle.world_id == world_id,
            ConstitutionArticle.is_active.is_(True),
        )
        .order_by(ConstitutionArticle.position)
    ).all()
    definitions = session.scalars(
        select(ToolDefinition)
        .where(ToolDefinition.is_active.is_(True))
        .order_by(ToolDefinition.name)
    ).all()
    events = list(
        reversed(
            session.scalars(
                select(WorldEvent)
                .where(WorldEvent.world_id == world_id)
                .order_by(WorldEvent.sequence_number.desc())
                .limit(20)
            ).all()
        )
    )
    builder = AgentContextBuilder(context_version=AUTONOMOUS_CONTEXT_VERSION)
    return builder.build(
        profile=AgentProfileView(
            agent_id=agent.id,
            name=agent.name,
            role=agent.role,
            personality=agent.personality,
            north_star_goal=agent.north_star_goal,
        ),
        state=AgentStateView(
            location=location.name,
            mood=state.mood,
            status=state.status.value,
            is_alive=state.is_alive,
            needs=NeedsView(
                energy=state.energy,
                knowledge=state.knowledge,
                influence=state.influence,
            ),
            compute_credits=state.cached_credit_balance,
        ),
        simulation_time=context_time,
        nearby_agents=[
            NearbyAgentView(
                agent_id=other.id,
                name=other.name,
                location=other_location.name,
                mood=other_state.mood,
                distance=0,
            )
            for other, other_state, other_location in nearby
        ],
        memories=memories,
        relationships=[
            RelationshipView(
                target_agent_id=target.id,
                target_name=target.name,
                relationship_type=relationship.relationship_type,
                rationale=relationship.rationale,
                interaction_count=relationship.interaction_count,
            )
            for relationship, target in relationships
        ],
        constitution=[
            ConstitutionArticleView(
                article_id=article.id,
                position=article.position,
                title=article.title,
                content=article.content,
            )
            for article in constitution
        ],
        available_tools=[
            ToolDefinitionView(
                name=definition.name,
                version=definition.version,
                description=definition.description,
                argument_schema=definition.argument_schema,
            )
            for definition in definitions
            if _available_at(definition, location.name)
        ],
        recent_events=[
            RecentEventView(
                event_id=event.id,
                event_type=event.event_type,
                payload=event.payload_json,
                simulation_time=event.simulation_time.replace(tzinfo=timezone),
            )
            for event in events
        ],
    )


def _available_at(definition: ToolDefinition, location: str) -> bool:
    locations = definition.availability_rules.get("locations", [])
    return not locations or location in locations


def _memory_views(context: dict[str, Any], created_at: Any) -> list[MemoryView]:
    views: list[MemoryView] = []
    mappings: tuple[
        tuple[
            str,
            Literal["long_term", "summary", "soul", "diary", "conversation"],
        ],
        ...,
    ] = (
        ("soul", "soul"),
        ("diary", "diary"),
        ("conversations", "conversation"),
        ("episodic_memories", "long_term"),
        ("summaries", "summary"),
    )
    for source, kind in mappings:
        for item in context.get(source, []):
            views.append(
                MemoryView(
                    memory_id=item["id"],
                    kind=kind,
                    content=item["content"],
                    created_at=created_at,
                )
            )
    return views
