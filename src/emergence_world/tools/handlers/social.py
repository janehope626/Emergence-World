# 实现面对面发言、私信和会话记录等社交工具。
"""Deterministic communication and reaction-queue handlers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from emergence_world.db.models import (
    Agent,
    AgentState,
    ConversationRecord,
    Message,
    ReactionRequest,
    SimulationClock,
    World,
)
from emergence_world.tools.handlers.core import HandlerOutput, PendingEvent


def _record_conversations(
    session: Session,
    *,
    world_id: str,
    tool_call_id: str,
    owner_ids: list[str],
    speaker_id: str,
    target_id: str | None,
    channel: str,
    content: str,
) -> list[PendingEvent]:
    events: list[PendingEvent] = []
    for owner_id in dict.fromkeys(owner_ids):
        record = ConversationRecord(
            world_id=world_id,
            owner_agent_id=owner_id,
            speaker_id=speaker_id,
            target_agent_id=target_id,
            tool_call_id=tool_call_id,
            channel=channel,
            content=content,
        )
        session.add(record)
        session.flush()
        events.append(
            PendingEvent(
                "conversation_recorded",
                {
                    "conversation_id": record.id,
                    "agent_id": owner_id,
                    "speaker_id": speaker_id,
                    "target_agent_id": target_id,
                    "channel": channel,
                    "content": content,
                    "created_at": record.created_at.isoformat(),
                },
            )
        )
    return events


def _agent(session: Session, world_id: str, name: str) -> Agent:
    agent = session.scalar(
        select(Agent).where(Agent.world_id == world_id, Agent.name == name)
    )
    if agent is None:
        raise ValueError(f"unknown agent: {name}")
    return agent


def _listeners(
    session: Session, world_id: str, speaker_id: str, target_id: str | None
) -> list[Agent]:
    speaker_state = session.get(AgentState, speaker_id)
    world = session.get(World, world_id)
    assert speaker_state is not None and world is not None
    query = (
        select(Agent)
        .join(AgentState, AgentState.agent_id == Agent.id)
        .where(
            Agent.world_id == world_id,
            Agent.id != speaker_id,
            AgentState.is_alive.is_(True),
            AgentState.current_landmark_id == speaker_state.current_landmark_id,
        )
        .order_by(Agent.name)
    )
    agents = list(session.scalars(query).all())
    if target_id is not None:
        agents.sort(key=lambda agent: (agent.id != target_id, agent.name))
    return agents[: int(world.config_json["parameters"]["max_listeners"])]


def _speak(
    session: Session, world_id: str, arguments: dict[str, Any], target_name: str | None
) -> HandlerOutput:
    speaker_id = str(arguments.pop("_agent_id"))
    tool_call_id = str(arguments.pop("_tool_call_id"))
    target = _agent(session, world_id, target_name) if target_name else None
    listeners = _listeners(session, world_id, speaker_id, target.id if target else None)
    if target is not None and all(listener.id != target.id for listener in listeners):
        raise ValueError("target agent is not nearby")
    sequence = (
        session.scalar(
            select(func.max(ReactionRequest.sequence_number)).where(
                ReactionRequest.world_id == world_id
            )
        )
        or 0
    )
    events: list[PendingEvent] = [
        PendingEvent(
            "agent_spoke",
            {
                "agent_id": speaker_id,
                "target_agent_id": target.id if target else None,
                "content": arguments["content"],
                "listener_ids": [agent.id for agent in listeners],
            },
        )
    ]
    events.extend(
        _record_conversations(
            session,
            world_id=world_id,
            tool_call_id=tool_call_id,
            owner_ids=[speaker_id, *[listener.id for listener in listeners]],
            speaker_id=speaker_id,
            target_id=target.id if target else None,
            channel="say" if target else "speak",
            content=str(arguments["content"]),
        )
    )
    for offset, listener in enumerate(listeners, start=1):
        pending = session.scalar(
            select(ReactionRequest).where(
                ReactionRequest.world_id == world_id,
                ReactionRequest.agent_id == listener.id,
                ReactionRequest.consumed.is_(False),
            )
        )
        if pending is not None:
            continue
        session.add(
            ReactionRequest(
                world_id=world_id,
                agent_id=listener.id,
                source_event_id=tool_call_id,
                sequence_number=sequence + offset,
            )
        )
        events.append(
            PendingEvent(
                "reaction_enqueued",
                {
                    "agent_id": listener.id,
                    "queue_sequence": sequence + offset,
                    "source_agent_id": speaker_id,
                },
            )
        )
    return HandlerOutput(
        {"listeners": [agent.name for agent in listeners]}, tuple(events)
    )


def say_to_agent(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    return _speak(session, world_id, arguments, str(arguments["target"]))


def speak_to_all(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    return _speak(session, world_id, arguments, None)


def send_message(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    sender_id = str(arguments.pop("_agent_id"))
    tool_call_id = str(arguments.pop("_tool_call_id"))
    target = _agent(session, world_id, str(arguments["target"]))
    message = Message(
        world_id=world_id,
        sender_id=sender_id,
        recipient_id=target.id,
        content=str(arguments["content"]),
    )
    session.add(message)
    session.flush()
    events = [
        PendingEvent(
            "message_sent",
            {
                "agent_id": sender_id,
                "target_agent_id": target.id,
                "message_id": message.id,
            },
        )
    ]
    events.extend(
        _record_conversations(
            session,
            world_id=world_id,
            tool_call_id=tool_call_id,
            owner_ids=[sender_id, target.id],
            speaker_id=sender_id,
            target_id=target.id,
            channel="message",
            content=message.content,
        )
    )
    return HandlerOutput(
        {"message_id": message.id},
        tuple(events),
    )


def read_messages(
    session: Session, world_id: str, arguments: dict[str, Any]
) -> HandlerOutput:
    agent_id = str(arguments.pop("_agent_id"))
    arguments.pop("_tool_call_id")
    clock = session.get(SimulationClock, world_id)
    assert clock is not None
    messages = session.scalars(
        select(Message)
        .where(Message.world_id == world_id, Message.recipient_id == agent_id)
        .order_by(Message.created_at)
    ).all()
    for message in messages:
        message.read_at = message.read_at or clock.current_time
    return HandlerOutput(
        {
            "messages": [
                {
                    "id": message.id,
                    "content": message.content,
                    "sender_id": message.sender_id,
                }
                for message in messages
            ]
        },
        (
            PendingEvent(
                "messages_read",
                {
                    "agent_id": agent_id,
                    "message_ids": [message.id for message in messages],
                },
            ),
        )
        if messages
        else (),
    )


def ignore(session: Session, world_id: str, arguments: dict[str, Any]) -> HandlerOutput:
    del session, world_id
    agent_id = str(arguments.pop("_agent_id"))
    arguments.pop("_tool_call_id")
    return HandlerOutput(
        {"ignored": True},
        (PendingEvent("reaction_ignored", {"agent_id": agent_id}),),
    )


SOCIAL_HANDLERS = {
    "say_to_agent": say_to_agent,
    "speak_to_all": speak_to_all,
    "send_message": send_message,
    "read_messages": read_messages,
    "ignore": ignore,
}
