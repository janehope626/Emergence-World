"""Database-independent enumerations used by persistence models."""

from __future__ import annotations

from enum import StrEnum


class ExperimentStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class WorldStatus(StrEnum):
    INITIALIZING = "initializing"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentStatus(StrEnum):
    ACTIVE = "active"
    IDLE = "idle"
    CRITICAL = "critical"
    DEAD = "dead"
    REMOVED = "removed"


class TurnType(StrEnum):
    REGULAR = "regular"
    BOOST = "boost"
    REACTION = "reaction"
    SYSTEM = "system"


class TurnStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ToolCallStatus(StrEnum):
    REQUESTED = "requested"
    VALIDATION_FAILED = "validation_failed"
    SUCCEEDED = "succeeded"
    EXECUTION_FAILED = "execution_failed"
