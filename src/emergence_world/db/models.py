"""Portable core persistence models for the deterministic world kernel."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from emergence_world.db.base import Base, new_id, utc_now
from emergence_world.db.types import (
    AgentStatus,
    ExperimentStatus,
    ToolCallStatus,
    TurnStatus,
    TurnType,
    WorldStatus,
)

ID = String(36)


def enum_type(enum_class: type[Any], name: str) -> Enum:
    """Store enum values portably instead of creating native database enums."""

    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    config_version: Mapped[str] = mapped_column(String(50))
    random_seed: Mapped[int] = mapped_column(Integer)
    status: Mapped[ExperimentStatus] = mapped_column(
        enum_type(ExperimentStatus, "experiment_status"),
        default=ExperimentStatus.CREATED,
    )
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class World(Base):
    __tablename__ = "worlds"
    __table_args__ = (UniqueConstraint("experiment_id", "name"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str | None] = mapped_column(String(100))
    model_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[WorldStatus] = mapped_column(
        enum_type(WorldStatus, "world_status"), default=WorldStatus.INITIALIZING
    )
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class SimulationClock(Base):
    __tablename__ = "simulation_clocks"

    world_id: Mapped[str] = mapped_column(
        ForeignKey("worlds.id", ondelete="CASCADE"), primary_key=True
    )
    current_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_advanced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class Landmark(Base):
    __tablename__ = "landmarks"
    __table_args__ = (
        UniqueConstraint("world_id", "id", name="uq_landmarks_world_id_id"),
        UniqueConstraint("world_id", "name", name="uq_landmarks_world_id_name"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    world_id: Mapped[str] = mapped_column(
        ForeignKey("worlds.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("world_id", "id", name="uq_agents_world_id_id"),
        UniqueConstraint("world_id", "name", name="uq_agents_world_id_name"),
        ForeignKeyConstraint(
            ["world_id", "home_landmark_id"],
            ["landmarks.world_id", "landmarks.id"],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    world_id: Mapped[str] = mapped_column(
        ForeignKey("worlds.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(Text)
    personality: Mapped[str] = mapped_column(Text)
    north_star_goal: Mapped[str] = mapped_column(Text)
    profile_version: Mapped[str] = mapped_column(String(50))
    home_landmark_id: Mapped[str | None] = mapped_column(ID)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class AgentState(Base):
    __tablename__ = "agent_states"
    __table_args__ = (
        CheckConstraint("energy >= 0 AND energy <= 100", name="energy_range"),
        CheckConstraint("knowledge >= 0 AND knowledge <= 100", name="knowledge_range"),
        CheckConstraint("influence >= 0 AND influence <= 100", name="influence_range"),
        CheckConstraint("cached_credit_balance >= 0", name="credit_nonnegative"),
        ForeignKeyConstraint(
            ["world_id", "agent_id"],
            ["agents.world_id", "agents.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["world_id", "current_landmark_id"],
            ["landmarks.world_id", "landmarks.id"],
            ondelete="RESTRICT",
        ),
    )

    agent_id: Mapped[str] = mapped_column(ID, primary_key=True)
    world_id: Mapped[str] = mapped_column(
        ForeignKey("worlds.id", ondelete="CASCADE"), index=True
    )
    current_landmark_id: Mapped[str | None] = mapped_column(ID)
    status: Mapped[AgentStatus] = mapped_column(
        enum_type(AgentStatus, "agent_status"), default=AgentStatus.ACTIVE
    )
    is_alive: Mapped[bool] = mapped_column(Boolean, default=True)
    mood: Mapped[str] = mapped_column(String(200), default="neutral")
    energy: Mapped[float] = mapped_column(Float, default=100)
    knowledge: Mapped[float] = mapped_column(Float, default=100)
    influence: Mapped[float] = mapped_column(Float, default=100)
    cached_credit_balance: Mapped[int] = mapped_column(Integer, default=0)
    zero_energy_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Turn(Base):
    __tablename__ = "turns"
    __table_args__ = (
        UniqueConstraint(
            "world_id", "sequence_number", name="uq_turns_world_sequence"
        ),
        CheckConstraint("sequence_number >= 1", name="sequence_positive"),
        CheckConstraint("tool_call_budget >= 0", name="budget_nonnegative"),
        CheckConstraint("tool_calls_used >= 0", name="calls_used_nonnegative"),
        CheckConstraint("tool_calls_used <= tool_call_budget", name="calls_within_budget"),
        UniqueConstraint("world_id", "id", name="uq_turns_world_id_id"),
        ForeignKeyConstraint(
            ["world_id", "agent_id"],
            ["agents.world_id", "agents.id"],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    world_id: Mapped[str] = mapped_column(
        ForeignKey("worlds.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[str | None] = mapped_column(ID, index=True)
    sequence_number: Mapped[int] = mapped_column(Integer)
    turn_type: Mapped[TurnType] = mapped_column(enum_type(TurnType, "turn_type"))
    status: Mapped[TurnStatus] = mapped_column(
        enum_type(TurnStatus, "turn_status"), default=TurnStatus.PENDING
    )
    tool_call_budget: Mapped[int] = mapped_column(Integer)
    tool_calls_used: Mapped[int] = mapped_column(Integer, default=0)
    context_version: Mapped[str | None] = mapped_column(String(50))
    context_hash: Mapped[str | None] = mapped_column(String(64))
    context_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    provider: Mapped[str | None] = mapped_column(String(100))
    model_name: Mapped[str | None] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ToolDefinition(Base):
    __tablename__ = "tool_definitions"
    __table_args__ = (UniqueConstraint("name", "version"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(150))
    version: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text)
    argument_schema: Mapped[dict[str, Any]] = mapped_column(JSON)
    result_schema: Mapped[dict[str, Any]] = mapped_column(JSON)
    availability_rules: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    produced_event_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class SeedDocument(Base):
    __tablename__ = "seed_documents"
    __table_args__ = (UniqueConstraint("world_id", "document_type", "version"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    world_id: Mapped[str] = mapped_column(
        ForeignKey("worlds.id", ondelete="CASCADE"), index=True
    )
    document_type: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(50))
    content: Mapped[str] = mapped_column(Text)
    source_path: Mapped[str] = mapped_column(String(300))
    source_sha256: Mapped[str] = mapped_column(String(64))


class ConstitutionArticle(Base):
    __tablename__ = "constitution_articles"
    __table_args__ = (
        UniqueConstraint("world_id", "position"),
        UniqueConstraint("world_id", "title"),
        CheckConstraint("position >= 1", name="position_positive"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    world_id: Mapped[str] = mapped_column(
        ForeignKey("worlds.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(String(50))
    source_path: Mapped[str] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class ToolCall(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        UniqueConstraint("turn_id", "sequence_number"),
        UniqueConstraint("world_id", "id", name="uq_tool_calls_world_id_id"),
        CheckConstraint("sequence_number >= 1", name="sequence_positive"),
        ForeignKeyConstraint(
            ["world_id", "turn_id"],
            ["turns.world_id", "turns.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["world_id", "agent_id"],
            ["agents.world_id", "agents.id"],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    world_id: Mapped[str] = mapped_column(
        ForeignKey("worlds.id", ondelete="CASCADE"), index=True
    )
    turn_id: Mapped[str] = mapped_column(ID, index=True)
    agent_id: Mapped[str] = mapped_column(ID, index=True)
    tool_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("tool_definitions.id", ondelete="SET NULL")
    )
    sequence_number: Mapped[int] = mapped_column(Integer)
    tool_name: Mapped[str] = mapped_column(String(150))
    tool_version: Mapped[str] = mapped_column(String(50))
    arguments_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[ToolCallStatus] = mapped_column(
        enum_type(ToolCallStatus, "tool_call_status"),
        default=ToolCallStatus.REQUESTED,
    )
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorldEvent(Base):
    __tablename__ = "world_events"
    __table_args__ = (
        UniqueConstraint("world_id", "sequence_number"),
        CheckConstraint("sequence_number >= 1", name="sequence_positive"),
        CheckConstraint(
            "(tool_call_id IS NOT NULL AND system_rule IS NULL) OR "
            "(tool_call_id IS NULL AND system_rule IS NOT NULL)",
            name="single_attribution",
        ),
        ForeignKeyConstraint(
            ["world_id", "turn_id"],
            ["turns.world_id", "turns.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["world_id", "tool_call_id"],
            ["tool_calls.world_id", "tool_calls.id"],
            ondelete="RESTRICT",
        ),
        Index("ix_world_events_world_type", "world_id", "event_type"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    world_id: Mapped[str] = mapped_column(
        ForeignKey("worlds.id", ondelete="CASCADE"), index=True
    )
    turn_id: Mapped[str | None] = mapped_column(ID, index=True)
    tool_call_id: Mapped[str | None] = mapped_column(ID, index=True)
    system_rule: Mapped[str | None] = mapped_column(String(150))
    sequence_number: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(150))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    simulation_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class CreditLedgerEntry(Base):
    __tablename__ = "credit_ledger_entries"
    __table_args__ = (
        CheckConstraint("amount != 0", name="amount_nonzero"),
        CheckConstraint(
            "(tool_call_id IS NOT NULL AND system_rule IS NULL) OR "
            "(tool_call_id IS NULL AND system_rule IS NOT NULL)",
            name="single_attribution",
        ),
        ForeignKeyConstraint(
            ["world_id", "agent_id"],
            ["agents.world_id", "agents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["world_id", "tool_call_id"],
            ["tool_calls.world_id", "tool_calls.id"],
            ondelete="RESTRICT",
        ),
        Index("ix_credit_ledger_world_agent", "world_id", "agent_id"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    world_id: Mapped[str] = mapped_column(
        ForeignKey("worlds.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[str] = mapped_column(ID, index=True)
    tool_call_id: Mapped[str | None] = mapped_column(ID, index=True)
    system_rule: Mapped[str | None] = mapped_column(String(150))
    amount: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(200))
    simulation_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
