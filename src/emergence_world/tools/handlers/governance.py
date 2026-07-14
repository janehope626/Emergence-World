# 实现提案提交、投票、评论、决议执行和宪法读取等治理工具。
"""Deterministic proposal and voting handlers."""

from __future__ import annotations

from math import ceil
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from emergence_world.db.models import (
    Agent,
    ConstitutionArticle,
    AgentState,
    Proposal,
    ProposalComment,
    ProposalVote,
    SimulationClock,
    World,
)
from emergence_world.db.types import AgentStatus, ProposalStatus
from emergence_world.tools.handlers.core import HandlerOutput, PendingEvent


def _proposal(session: Session, world_id: str, proposal_id: str) -> Proposal:
    proposal = session.scalar(
        select(Proposal).where(
            Proposal.world_id == world_id, Proposal.id == proposal_id
        )
    )
    if proposal is None:
        raise ValueError("proposal not found")
    return proposal


def _resolve(session: Session, proposal: Proposal) -> PendingEvent | None:
    world = session.get(World, proposal.world_id)
    clock = session.get(SimulationClock, proposal.world_id)
    assert world is not None and clock is not None
    live = (
        session.scalar(
            select(func.count())
            .select_from(AgentState)
            .where(
                AgentState.world_id == proposal.world_id, AgentState.is_alive.is_(True)
            )
        )
        or 0
    )
    threshold = ceil(
        live * float(world.config_json["parameters"]["governance_approval_threshold"])
    )
    votes = session.scalars(
        select(ProposalVote).where(ProposalVote.proposal_id == proposal.id)
    ).all()
    for_votes = sum(vote.choice == "for" for vote in votes)
    remaining = live - len(votes)
    consequence = None
    if for_votes >= threshold:
        proposal.status = ProposalStatus.ACCEPTED
        consequence = _apply_action(session, proposal)
    elif for_votes + remaining < threshold:
        proposal.status = ProposalStatus.REJECTED
    else:
        return None
    proposal.resolved_at = clock.current_time
    return PendingEvent(
        "proposal_resolved",
        {
            "proposal_id": proposal.id,
            "status": proposal.status.value,
            "for_votes": for_votes,
            "threshold": threshold,
            "consequence": consequence
            if proposal.status == ProposalStatus.ACCEPTED
            else None,
        },
    )


def _apply_action(session: Session, proposal: Proposal) -> dict[str, Any] | None:
    action = proposal.action_json
    action_type = action.get("type")
    if action_type == "remove_agent":
        state = session.scalar(
            select(AgentState)
            .join(Agent, Agent.id == AgentState.agent_id)
            .where(
                Agent.world_id == proposal.world_id, Agent.name == action.get("name")
            )
        )
        if state is None:
            raise ValueError("governance target agent not found")
        state.is_alive = False
        state.status = AgentStatus.REMOVED
        return {"type": "remove_agent", "agent_id": state.agent_id}
    if action_type in {"add_article", "replace_article"}:
        position = int(action["position"])
        article = session.scalar(
            select(ConstitutionArticle).where(
                ConstitutionArticle.world_id == proposal.world_id,
                ConstitutionArticle.position == position,
            )
        )
        if article is None:
            article = ConstitutionArticle(
                world_id=proposal.world_id,
                position=position,
                title=str(action["title"]),
                content=str(action["content"]),
                version="governance-v1",
                source_path="governance",
            )
            session.add(article)
        else:
            article.title = str(action["title"])
            article.content = str(action["content"])
            article.version = "governance-v1"
        return {"type": action_type, "position": position, "title": article.title}
    if action_type == "remove_article":
        article = session.scalar(
            select(ConstitutionArticle).where(
                ConstitutionArticle.world_id == proposal.world_id,
                ConstitutionArticle.position == int(action["position"]),
            )
        )
        if article is None:
            raise ValueError("constitution article not found")
        article.is_active = False
        return {
            "type": action_type,
            "position": article.position,
            "title": article.title,
        }
    return None


def submit_townhall_proposal(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    proposer_id = str(arguments.pop("_agent_id"))
    arguments.pop("_tool_call_id")
    proposal = Proposal(
        world_id=world_id,
        proposer_id=proposer_id,
        title=str(arguments["title"]),
        description=str(arguments["description"]),
        category=str(arguments.get("category", "others")),
        action_json=dict(arguments.get("action", {})),
    )
    session.add(proposal)
    session.flush()
    session.add(
        ProposalVote(
            world_id=world_id,
            proposal_id=proposal.id,
            agent_id=proposer_id,
            choice="for",
            implicit=True,
        )
    )
    return HandlerOutput(
        {"proposal_id": proposal.id, "status": proposal.status.value},
        (
            PendingEvent(
                "proposal_submitted",
                {
                    "proposal_id": proposal.id,
                    "agent_id": proposer_id,
                    "title": proposal.title,
                },
            ),
            PendingEvent(
                "vote_cast",
                {
                    "proposal_id": proposal.id,
                    "agent_id": proposer_id,
                    "choice": "for",
                    "implicit": True,
                },
            ),
        ),
    )


def vote_on_proposal(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    agent_id = str(arguments.pop("_agent_id"))
    arguments.pop("_tool_call_id")
    proposal = _proposal(session, world_id, str(arguments["proposal_id"]))
    if proposal.status != ProposalStatus.ACTIVE:
        raise ValueError("proposal is not active")
    session.add(
        ProposalVote(
            world_id=world_id,
            proposal_id=proposal.id,
            agent_id=agent_id,
            choice=str(arguments["choice"]),
        )
    )
    session.flush()
    events = [
        PendingEvent(
            "vote_cast",
            {
                "proposal_id": proposal.id,
                "agent_id": agent_id,
                "choice": arguments["choice"],
                "implicit": False,
            },
        )
    ]
    resolved = _resolve(session, proposal)
    if resolved:
        events.append(resolved)
    return HandlerOutput(
        {"proposal_id": proposal.id, "status": proposal.status.value}, tuple(events)
    )


def list_proposals(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    arguments.pop("_agent_id")
    arguments.pop("_tool_call_id")
    proposals = session.scalars(
        select(Proposal)
        .where(Proposal.world_id == world_id)
        .order_by(Proposal.created_at)
    ).all()
    return HandlerOutput(
        {
            "proposals": [
                {"id": p.id, "title": p.title, "status": p.status.value}
                for p in proposals
            ]
        }
    )


def comment_on_proposal(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    agent_id = str(arguments.pop("_agent_id"))
    arguments.pop("_tool_call_id")
    proposal = _proposal(session, world_id, str(arguments["proposal_id"]))
    comment = ProposalComment(
        world_id=world_id,
        proposal_id=proposal.id,
        agent_id=agent_id,
        content=str(arguments["content"]),
    )
    session.add(comment)
    session.flush()
    return HandlerOutput(
        {"comment_id": comment.id},
        (
            PendingEvent(
                "proposal_commented",
                {
                    "proposal_id": proposal.id,
                    "agent_id": agent_id,
                    "comment_id": comment.id,
                },
            ),
        ),
    )


def read_constitution(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    arguments.pop("_agent_id")
    arguments.pop("_tool_call_id")
    articles = session.scalars(
        select(ConstitutionArticle)
        .where(
            ConstitutionArticle.world_id == world_id,
            ConstitutionArticle.is_active.is_(True),
        )
        .order_by(ConstitutionArticle.position)
    ).all()
    return HandlerOutput(
        {
            "articles": [
                {
                    "position": article.position,
                    "title": article.title,
                    "content": article.content,
                }
                for article in articles
            ]
        }
    )


GOVERNANCE_HANDLERS = {
    "submit_townhall_proposal": submit_townhall_proposal,
    "vote_on_proposal": vote_on_proposal,
    "list_proposals": list_proposals,
    "comment_on_proposal": comment_on_proposal,
    "read_constitution": read_constitution,
}
