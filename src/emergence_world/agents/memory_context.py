"""Audited deterministic construction of an agent's private memory context."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from json import dumps
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from emergence_world.db.models import (
    Agent,
    ContextBuild,
    ContextMemoryCandidate,
    ContextMemorySelection,
    ConversationRecord,
    DiaryEntry,
    EpisodicMemory,
    MemorySummary,
    Relationship,
    SoulEntry,
)

CONTEXT_POLICY_VERSION = "memory_context_v1"


@dataclass(frozen=True, slots=True)
class MemoryContext:
    context_hash: str
    context: dict[str, Any]
    build_id: str


@dataclass(slots=True)
class _Candidate:
    kind: str
    source_id: str
    score: float
    value: dict[str, Any]
    selected: bool = False
    exclusion_reason: str | None = None


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", text.lower()))


def _stable_hash(value: dict[str, Any]) -> str:
    canonical = dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return sha256(canonical.encode("utf-8")).hexdigest()


def build_memory_context(
    session: Session,
    *,
    world_id: str,
    agent_id: str,
    query: str = "",
    episodic_limit: int = 10,
    recent_limit: int = 5,
) -> MemoryContext:
    """Build and audit a context using only the requesting agent's private state."""

    agent = session.get(Agent, agent_id)
    if agent is None or agent.world_id != world_id:
        raise ValueError("agent not found in world")
    candidates: list[_Candidate] = []

    soul = session.scalars(
        select(SoulEntry)
        .where(
            SoulEntry.world_id == world_id,
            SoulEntry.agent_id == agent_id,
            SoulEntry.active.is_(True),
        )
        .order_by(SoulEntry.created_at, SoulEntry.id)
    ).all()
    for soul_entry in soul:
        candidates.append(
            _Candidate(
                "soul",
                soul_entry.id,
                1.0,
                {"id": soul_entry.id, "content": soul_entry.current_content},
                selected=True,
            )
        )

    diaries = session.scalars(
        select(DiaryEntry)
        .where(DiaryEntry.world_id == world_id, DiaryEntry.agent_id == agent_id)
        .order_by(DiaryEntry.simulation_date.desc(), DiaryEntry.id)
    ).all()
    for index, diary_entry in enumerate(diaries):
        candidates.append(
            _Candidate(
                "diary",
                diary_entry.id,
                1 / (index + 1),
                {
                    "id": diary_entry.id,
                    "date": diary_entry.simulation_date,
                    "content": diary_entry.current_content,
                },
                selected=index < recent_limit,
                exclusion_reason=None if index < recent_limit else "outside_recent_limit",
            )
        )

    conversations = session.scalars(
        select(ConversationRecord)
        .where(
            ConversationRecord.world_id == world_id,
            ConversationRecord.owner_agent_id == agent_id,
        )
        .order_by(ConversationRecord.created_at.desc(), ConversationRecord.id)
    ).all()
    for index, record in enumerate(conversations):
        candidates.append(
            _Candidate(
                "conversation",
                record.id,
                1 / (index + 1),
                {
                    "id": record.id,
                    "speaker_id": record.speaker_id,
                    "target_agent_id": record.target_agent_id,
                    "channel": record.channel,
                    "content": record.content,
                },
                selected=index < recent_limit,
                exclusion_reason=None if index < recent_limit else "outside_recent_limit",
            )
        )

    rows = session.execute(
        select(Relationship, Agent)
        .join(Agent, Agent.id == Relationship.target_agent_id)
        .where(
            Relationship.world_id == world_id,
            Relationship.observer_agent_id == agent_id,
        )
        .order_by(Agent.name)
    ).all()
    for relationship, target in rows:
        candidates.append(
            _Candidate(
                "relationship",
                relationship.id,
                1.0,
                {
                    "id": relationship.id,
                    "target": target.name,
                    "relationship_type": relationship.relationship_type,
                    "rationale": relationship.rationale,
                    "trust_score": relationship.trust_score,
                    "affinity_score": relationship.affinity_score,
                },
                selected=True,
            )
        )

    query_tokens = _tokens(query)
    memories = session.scalars(
        select(EpisodicMemory)
        .where(
            EpisodicMemory.world_id == world_id,
            EpisodicMemory.agent_id == agent_id,
        )
        .order_by(EpisodicMemory.created_at.desc(), EpisodicMemory.id)
    ).all()
    episodic_candidates: list[_Candidate] = []
    for memory in memories:
        overlap = len(query_tokens & (_tokens(memory.content) | set(memory.tags_json)))
        score = float(overlap) + memory.importance
        reason = None
        if not memory.active:
            reason = "archived"
        elif query_tokens and overlap == 0:
            reason = "no_keyword_match"
        candidate = _Candidate(
            "episodic",
            memory.id,
            score,
            {
                "id": memory.id,
                "content": memory.content,
                "importance": memory.importance,
                "tags": memory.tags_json,
            },
            exclusion_reason=reason,
        )
        episodic_candidates.append(candidate)
        candidates.append(candidate)
    eligible = sorted(
        (candidate for candidate in episodic_candidates if candidate.exclusion_reason is None),
        key=lambda candidate: (-candidate.score, candidate.source_id),
    )
    for index, candidate in enumerate(eligible):
        candidate.selected = index < episodic_limit
        if not candidate.selected:
            candidate.exclusion_reason = "outside_episodic_limit"

    if not any(candidate.selected for candidate in episodic_candidates):
        summaries = session.scalars(
            select(MemorySummary)
            .where(
                MemorySummary.world_id == world_id,
                MemorySummary.agent_id == agent_id,
            )
            .order_by(MemorySummary.created_at.desc(), MemorySummary.id)
        ).all()
        for index, summary in enumerate(summaries):
            candidates.append(
                _Candidate(
                    "summary",
                    summary.id,
                    1 / (index + 1),
                    {
                        "id": summary.id,
                        "algorithm": summary.algorithm,
                        "content": summary.content,
                        "source_count": summary.source_count,
                    },
                    selected=index < recent_limit,
                    exclusion_reason=None if index < recent_limit else "outside_recent_limit",
                )
            )

    context = {
        "agent": agent.name,
        "query": query,
        "soul": [item.value for item in candidates if item.kind == "soul" and item.selected],
        "diary": [item.value for item in candidates if item.kind == "diary" and item.selected],
        "conversations": [
            item.value for item in candidates if item.kind == "conversation" and item.selected
        ],
        "relationships": [
            item.value for item in candidates if item.kind == "relationship" and item.selected
        ],
        "episodic_memories": [
            item.value for item in candidates if item.kind == "episodic" and item.selected
        ],
        "summaries": [
            item.value for item in candidates if item.kind == "summary" and item.selected
        ],
    }
    context_hash = _stable_hash(context)
    build = ContextBuild(
        world_id=world_id,
        agent_id=agent_id,
        query=query,
        policy_version=CONTEXT_POLICY_VERSION,
        context_hash=context_hash,
        context_json=context,
    )
    session.add(build)
    session.flush()
    rank = 0
    for item in candidates:
        db_candidate = ContextMemoryCandidate(
            context_build_id=build.id,
            world_id=world_id,
            agent_id=agent_id,
            memory_kind=item.kind,
            source_id=item.source_id,
            score=item.score,
            exclusion_reason=item.exclusion_reason,
        )
        session.add(db_candidate)
        session.flush()
        if item.selected:
            rank += 1
            session.add(
                ContextMemorySelection(
                    context_build_id=build.id,
                    candidate_id=db_candidate.id,
                    rank=rank,
                )
            )
    return MemoryContext(context_hash=context_hash, context=context, build_id=build.id)
