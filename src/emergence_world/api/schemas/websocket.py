"""WebSocket event contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TraceStreamEvent(BaseModel):
    type: str
    event_id: str
    command_id: str | None = None
    world_id: str | None = None
    turn_id: str | None = None
    sequence: int | None = None
    stream_sequence: int | None = None
    provisional: bool = True
    timestamp: datetime
    data: dict[str, Any] = Field(default_factory=dict)
