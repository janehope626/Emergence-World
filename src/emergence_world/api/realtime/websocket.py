"""Trace WebSocket endpoint."""

from __future__ import annotations

import asyncio
from time import monotonic

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session, sessionmaker

from emergence_world.observability.query import (
    committed_trace_stream_events,
    latest_trace_stream_sequence,
)
from emergence_world.observability.stream import (
    trace_event_broker,
    trace_stream_event,
)

router = APIRouter(tags=["trace-stream"])


@router.websocket("/ws/v1/traces")
async def trace_stream(
    websocket: WebSocket,
    world_id: str | None = Query(None),
    command_id: str | None = Query(None),
    after_sequence: int | None = Query(None, ge=0),
) -> None:
    await websocket.accept()
    factory: sessionmaker[Session] = websocket.app.state.session_factory

    def initial_cursor() -> int:
        if after_sequence is not None:
            return after_sequence
        with factory() as session:
            return latest_trace_stream_sequence(session)

    def poll(cursor: int) -> tuple[int, list[dict[str, object]]]:
        with factory() as session:
            return committed_trace_stream_events(
                session,
                after_sequence=cursor,
                world_id=world_id,
                command_id=command_id,
            )

    cursor = await asyncio.to_thread(initial_cursor)
    subscription = trace_event_broker.subscribe(
        world_id=world_id, command_id=command_id
    )
    await websocket.send_json(
        trace_stream_event(
            "stream.connected",
            world_id=world_id,
            command_id=command_id,
            provisional=False,
            data={
                "reconcile_via": "/api/v1/traces",
                "after_sequence": cursor,
            },
        )
    )
    last_heartbeat = monotonic()
    try:
        while True:
            try:
                event = await asyncio.wait_for(subscription.queue.get(), timeout=0.25)
            except TimeoutError:
                event = None
            if event is not None:
                await websocket.send_json(event)
            cursor, committed = await asyncio.to_thread(poll, cursor)
            for persisted in committed:
                await websocket.send_json(persisted)
            if monotonic() - last_heartbeat >= 20:
                event = trace_stream_event(
                    "heartbeat",
                    world_id=world_id,
                    command_id=command_id,
                    provisional=False,
                    data={"after_sequence": cursor},
                )
                await websocket.send_json(event)
                last_heartbeat = monotonic()
    except WebSocketDisconnect:
        pass
    finally:
        trace_event_broker.unsubscribe(subscription)
