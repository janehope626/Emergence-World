"""Recursive redaction for persisted observability payloads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Any

from emergence_world.agents.providers.security import redact_secrets

SENSITIVE_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)


def redact(value: Any) -> Any:
    return redact_secrets(_normalize(value))


def _normalize(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "model_dump"):
        return _normalize(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]" if _sensitive(str(key)) else _normalize(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize(item) for item in value]
    return repr(value)


def _sensitive(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(fragment in normalized for fragment in SENSITIVE_FRAGMENTS)
