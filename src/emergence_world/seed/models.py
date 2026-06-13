"""Validated contracts for versioned structured seed data."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SeedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorldDefaults(SeedModel):
    name: str
    simulation_start: datetime
    initial_location: str
    initial_energy: float = Field(ge=0, le=100)
    initial_knowledge: float = Field(ge=0, le=100)
    initial_influence: float = Field(ge=0, le=100)
    initial_credits: int = Field(ge=0)
    parameters: dict[str, Any]
    reproduction_assumptions: tuple[str, ...]

    @model_validator(mode="after")
    def validate_start_time(self) -> WorldDefaults:
        if self.simulation_start.tzinfo is None:
            raise ValueError("simulation_start must be timezone-aware")
        return self


class AgentSeed(SeedModel):
    name: str
    version: str
    role: str
    personality: str
    north_star_goal: str
    home: str


class LandmarkSeed(SeedModel):
    name: str
    category: str
    description: str
    gated_tools: tuple[str, ...] = ()
    source_path: str = "docs/landmarks/README.md"


class ToolSeed(SeedModel):
    name: str
    version: str = "reproduction-0.1"
    description: str
    locations: tuple[str, ...] = ()
    argument_schema: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    )
    result_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object"}
    )
    produced_event_types: tuple[str, ...] = ()


class ConstitutionArticleSeed(SeedModel):
    position: int = Field(ge=1)
    title: str
    content: str


class DocumentSeed(SeedModel):
    document_type: str
    title: str
    version: str
    source_path: str
    content: str


class SeedBundle(SeedModel):
    seed_version: str
    attribution: str
    source_repository: str
    world: WorldDefaults
    agents: tuple[AgentSeed, ...]
    landmarks: tuple[LandmarkSeed, ...]
    tools: tuple[ToolSeed, ...]
    constitution: tuple[ConstitutionArticleSeed, ...]
    documents: tuple[DocumentSeed, ...]

    @model_validator(mode="after")
    def validate_references(self) -> SeedBundle:
        landmark_names = {landmark.name for landmark in self.landmarks}
        tool_names = {tool.name for tool in self.tools}
        if len(landmark_names) != len(self.landmarks):
            raise ValueError("landmark names must be unique")
        if len(tool_names) != len(self.tools):
            raise ValueError("tool names must be unique")
        if len({agent.name for agent in self.agents}) != len(self.agents):
            raise ValueError("agent names must be unique")
        if self.world.initial_location not in landmark_names:
            raise ValueError("initial_location must reference a seeded landmark")
        for agent in self.agents:
            if agent.home not in landmark_names:
                raise ValueError(f"unknown home landmark for {agent.name}")
        for landmark in self.landmarks:
            unknown = set(landmark.gated_tools) - tool_names
            if unknown:
                raise ValueError(f"unknown gated tools for {landmark.name}: {unknown}")
        return self


DEFAULT_SEED_PATH = Path(__file__).with_name("data") / "season_1_reproduction_v1.yaml"


def load_seed_bundle(path: Path = DEFAULT_SEED_PATH) -> SeedBundle:
    with path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    return SeedBundle.model_validate(raw)
