"""Versioned tool definition and handler registry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from emergence_world.db.models import ToolDefinition
from emergence_world.tools.handlers.core import CORE_HANDLERS, HandlerOutput

ToolHandler = Callable[[Session, str, dict[str, Any]], HandlerOutput]


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    definition: ToolDefinition
    handler: ToolHandler | None


class ToolRegistry:
    def __init__(self, handlers: Mapping[str, ToolHandler] | None = None) -> None:
        self._handlers = dict(CORE_HANDLERS if handlers is None else handlers)

    def get(self, session: Session, tool_name: str) -> RegisteredTool | None:
        definition = session.scalar(
            select(ToolDefinition).where(
                ToolDefinition.name == tool_name,
                ToolDefinition.is_active.is_(True),
            )
        )
        if definition is None:
            return None
        return RegisteredTool(definition, self._handlers.get(tool_name))
