"""Versioned provider prompt loading and hashing."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

AGENT_TURN_PROMPT_VERSION = "agent_turn_v1"
DEFAULT_PROMPT_PATH = Path(__file__).parents[3] / "prompts" / "agent_turn_v1.md"


def load_agent_turn_prompt(path: Path = DEFAULT_PROMPT_PATH) -> str:
    return path.read_text(encoding="utf-8")


def agent_turn_prompt_hash(path: Path = DEFAULT_PROMPT_PATH) -> str:
    return sha256(load_agent_turn_prompt(path).encode("utf-8")).hexdigest()
