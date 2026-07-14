# 识别并遮蔽配置中的敏感值，防止密钥进入审计与追踪数据。
"""Secret redaction and leak detection for provider audit boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from os import environ
from typing import Any

SENSITIVE_KEY_MARKERS = ("api_key", "apikey", "authorization", "access_token", "secret")


def configured_secrets() -> tuple[str, ...]:
    return tuple(
        value
        for key, value in environ.items()
        if value and any(marker in key.lower() for marker in SENSITIVE_KEY_MARKERS)
    )


def redact_secrets(value: Any) -> Any:
    secrets = configured_secrets()
    return _redact(value, secrets)


def contains_configured_secret(value: Any) -> bool:
    serialized = repr(value)
    return any(secret in serialized for secret in configured_secrets())


def _redact(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if any(marker in str(key).lower() for marker in SENSITIVE_KEY_MARKERS)
                else _redact(item, secrets)
            )
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, str):
        redacted = value
        for secret in secrets:
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    return value
