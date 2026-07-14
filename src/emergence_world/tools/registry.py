# 保存工具定义与处理器映射，并提供名称解析和注册校验。
"""Versioned tool definition and handler registry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from emergence_world.db.models import ToolDefinition
from emergence_world.tools.handlers.core import CORE_HANDLERS, HandlerOutput
from emergence_world.tools.handlers.economy import ECONOMY_HANDLERS
from emergence_world.tools.handlers.governance import GOVERNANCE_HANDLERS
from emergence_world.tools.handlers.memory import MEMORY_HANDLERS
from emergence_world.tools.handlers.social import SOCIAL_HANDLERS
from emergence_world.tools.handlers.pitches import PITCH_HANDLERS

ToolHandler = Callable[[Session, str, dict[str, Any]], HandlerOutput]


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    definition: ToolDefinition
    handler: ToolHandler | None


class ToolRegistry:
    def __init__(self, handlers: Mapping[str, ToolHandler] | None = None) -> None:
        defaults = {
            **CORE_HANDLERS,
            **ECONOMY_HANDLERS,
            **SOCIAL_HANDLERS,
            **MEMORY_HANDLERS,
            **GOVERNANCE_HANDLERS,
            **PITCH_HANDLERS,
        }
        self._handlers = dict(defaults if handlers is None else handlers)

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
