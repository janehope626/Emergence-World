# 提供确定性的长期记忆摘要算法。
"""Deterministic memory consolidation primitives."""

from __future__ import annotations

from collections import Counter

from emergence_world.db.models import EpisodicMemory

SUMMARY_ALGORITHM = "deterministic_summary_v1"
SELF_CARE_MINIMUM = 30
SELF_CARE_BATCH_SIZE = 500


def deterministic_summary_v1(memories: list[EpisodicMemory]) -> str:
    """Build a stable, non-LLM summary from an ordered memory batch."""

    if not memories:
        raise ValueError("cannot summarize an empty memory batch")
    tags = Counter(tag for memory in memories for tag in memory.tags_json)
    top_tags = ", ".join(
        f"{tag}:{count}" for tag, count in sorted(tags.items(), key=lambda item: (-item[1], item[0]))[:10]
    ) or "none"
    highlights = sorted(
        memories,
        key=lambda memory: (-memory.importance, memory.created_at, memory.id),
    )[:5]
    highlight_text = " | ".join(memory.content for memory in highlights)
    return (
        f"Consolidated {len(memories)} memories. "
        f"Top tags: {top_tags}. "
        f"High-importance observations: {highlight_text}"
    )
