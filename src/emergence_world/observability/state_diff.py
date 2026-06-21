"""Stable leaf-level differences for canonical world snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MISSING = {"__trace_missing__": True}


@dataclass(frozen=True, slots=True)
class SnapshotChange:
    entity_type: str
    entity_id: str
    path: str
    before: Any
    after: Any


def diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> tuple[SnapshotChange, ...]:
    raw: list[tuple[tuple[str, ...], Any, Any]] = []
    _walk((), before, after, raw)
    return tuple(_to_change(path, old, new) for path, old, new in raw)


def _walk(
    path: tuple[str, ...], before: Any, after: Any, output: list[tuple[tuple[str, ...], Any, Any]]
) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            if key not in before:
                _walk(path + (str(key),), MISSING, after[key], output)
            elif key not in after:
                _walk(path + (str(key),), before[key], MISSING, output)
            else:
                _walk(path + (str(key),), before[key], after[key], output)
        return
    if isinstance(before, list) and isinstance(after, list):
        if before != after:
            output.append((path, before, after))
        return
    if before != after:
        output.append((path, before, after))


def _to_change(path: tuple[str, ...], before: Any, after: Any) -> SnapshotChange:
    if len(path) >= 2 and path[0] == "agents":
        entity_type, entity_id, relative = "agent", path[1], path[2:]
    elif len(path) >= 2 and path[0] == "memory":
        entity_type, entity_id, relative = "agent_memory", path[1], path[2:]
    else:
        entity_type, entity_id, relative = "world", "world", path
    pointer = "/" + "/".join(_escape(part) for part in relative)
    return SnapshotChange(entity_type, entity_id, pointer, before, after)


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")
