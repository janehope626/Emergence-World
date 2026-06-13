"""Transactional and idempotent import of validated seed bundles."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from emergence_world.db.models import (
    Agent,
    AgentState,
    ConstitutionArticle,
    Experiment,
    Landmark,
    SeedDocument,
    SimulationClock,
    ToolDefinition,
    World,
)
from emergence_world.db.types import WorldStatus
from emergence_world.seed.models import SeedBundle


@dataclass(frozen=True, slots=True)
class SeedImportResult:
    experiment_id: str
    world_id: str
    created: bool
    agents: int
    landmarks: int
    tools: int
    constitution_articles: int


def import_seed_bundle(
    session: Session,
    bundle: SeedBundle,
    *,
    experiment_name: str = "Emergence World Season 1 Reproduction",
    random_seed: int = 1,
) -> SeedImportResult:
    """Import one complete world; caller owns the transaction boundary."""

    existing = session.scalar(
        select(World)
        .join(Experiment)
        .where(
            Experiment.name == experiment_name,
            Experiment.config_version == bundle.seed_version,
            World.name == bundle.world.name,
        )
    )
    if existing is not None:
        _upsert_tools(session, bundle)
        session.flush()
        return _result(session, existing, created=False)

    experiment = Experiment(
        name=experiment_name,
        config_version=bundle.seed_version,
        random_seed=random_seed,
        config_json={
            "attribution": bundle.attribution,
            "source_repository": bundle.source_repository,
            "seed_version": bundle.seed_version,
        },
    )
    session.add(experiment)
    session.flush()

    world = World(
        experiment_id=experiment.id,
        name=bundle.world.name,
        status=WorldStatus.READY,
        config_json={
            "parameters": bundle.world.parameters,
            "reproduction_assumptions": list(bundle.world.reproduction_assumptions),
            "seed_version": bundle.seed_version,
        },
    )
    session.add(world)
    session.flush()
    session.add(
        SimulationClock(
            world_id=world.id,
            current_time=bundle.world.simulation_start,
            last_advanced_at=bundle.world.simulation_start,
        )
    )

    landmarks: dict[str, Landmark] = {}
    for landmark_seed in bundle.landmarks:
        landmark = Landmark(
            world_id=world.id,
            name=landmark_seed.name,
            category=landmark_seed.category,
            description=landmark_seed.description,
            metadata_json={
                "source_path": landmark_seed.source_path,
                "gated_tools": list(landmark_seed.gated_tools),
            },
        )
        session.add(landmark)
        landmarks[landmark_seed.name] = landmark
    session.flush()

    initial_location = landmarks[bundle.world.initial_location]
    for agent_seed in bundle.agents:
        agent = Agent(
            world_id=world.id,
            name=agent_seed.name,
            role=agent_seed.role,
            personality=agent_seed.personality,
            north_star_goal=agent_seed.north_star_goal,
            profile_version=agent_seed.version,
            home_landmark_id=landmarks[agent_seed.home].id,
        )
        session.add(agent)
        session.flush()
        session.add(
            AgentState(
                agent_id=agent.id,
                world_id=world.id,
                current_landmark_id=initial_location.id,
                energy=bundle.world.initial_energy,
                knowledge=bundle.world.initial_knowledge,
                influence=bundle.world.initial_influence,
                cached_credit_balance=bundle.world.initial_credits,
            )
        )

    for article_seed in bundle.constitution:
        session.add(
            ConstitutionArticle(
                world_id=world.id,
                position=article_seed.position,
                title=article_seed.title,
                content=article_seed.content,
                version=bundle.seed_version,
                source_path="docs/data/constitution.md",
            )
        )

    for document_seed in bundle.documents:
        session.add(
            SeedDocument(
                world_id=world.id,
                document_type=document_seed.document_type,
                title=document_seed.title,
                version=document_seed.version,
                content=document_seed.content,
                source_path=document_seed.source_path,
                source_sha256=sha256(document_seed.content.encode("utf-8")).hexdigest(),
            )
        )

    _upsert_tools(session, bundle)

    session.flush()
    return _result(session, world, created=True)


def _upsert_tools(session: Session, bundle: SeedBundle) -> None:
    for tool_seed in bundle.tools:
        locations = sorted(
            landmark.name
            for landmark in bundle.landmarks
            if tool_seed.name in landmark.gated_tools
        )
        existing_tool = session.scalar(
            select(ToolDefinition).where(
                ToolDefinition.name == tool_seed.name,
                ToolDefinition.version == tool_seed.version,
            )
        )
        if existing_tool is None:
            session.add(
                ToolDefinition(
                    name=tool_seed.name,
                    version=tool_seed.version,
                    description=tool_seed.description,
                    argument_schema=tool_seed.argument_schema,
                    result_schema=tool_seed.result_schema,
                    availability_rules={
                        "locations": locations or list(tool_seed.locations),
                        "schema_status": "reproduction_schema_v1",
                    },
                    produced_event_types=list(tool_seed.produced_event_types),
                )
            )
        else:
            existing_tool.description = tool_seed.description
            existing_tool.argument_schema = tool_seed.argument_schema
            existing_tool.result_schema = tool_seed.result_schema
            existing_tool.availability_rules = {
                "locations": locations or list(tool_seed.locations),
                "schema_status": "reproduction_schema_v1",
            }
            existing_tool.produced_event_types = list(tool_seed.produced_event_types)
            existing_tool.is_active = True


def _result(session: Session, world: World, *, created: bool) -> SeedImportResult:
    def count(model: type[object]) -> int:
        return len(session.scalars(select(model).where(model.world_id == world.id)).all())  # type: ignore[attr-defined]

    tools = len(session.scalars(select(ToolDefinition)).all())
    return SeedImportResult(
        experiment_id=world.experiment_id,
        world_id=world.id,
        created=created,
        agents=count(Agent),
        landmarks=count(Landmark),
        tools=tools,
        constitution_articles=count(ConstitutionArticle),
    )
