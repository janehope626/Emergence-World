# 在进程内分发已提交的追踪事件，并维护订阅者队列。
"""Thread-safe in-process delivery of lightweight live trace events."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from threading import Lock
from typing import Any
from uuid import uuid4

from emergence_world.db.base import utc_now


def trace_stream_event(
    event_type: str,
    *,
    command_id: str | None = None,
    world_id: str | None = None,
    turn_id: str | None = None,
    sequence: int | None = None,
    provisional: bool = True,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "type": event_type,
        "event_id": str(uuid4()),
        "command_id": command_id,
        "world_id": world_id,
        "turn_id": turn_id,
        "sequence": sequence,
        "provisional": provisional,
        "timestamp": utc_now().isoformat(),
        "data": data or {},
    }


@dataclass(slots=True)
class TraceSubscription:
    id: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[dict[str, Any]]
    world_id: str | None
    command_id: str | None


class TraceEventBroker:
    """Fan out events to bounded per-client queues across worker threads."""

    def __init__(self, *, queue_size: int = 256) -> None:
        if queue_size < 2:
            raise ValueError("trace stream queue_size must be at least 2")
        self.queue_size = queue_size
        self._subscriptions: dict[str, TraceSubscription] = {}
        self._lock = Lock()

    def subscribe(
        self,
        *,
        world_id: str | None = None,
        command_id: str | None = None,
    ) -> TraceSubscription:
        subscription = TraceSubscription(
            id=str(uuid4()),
            loop=asyncio.get_running_loop(),
            queue=asyncio.Queue(maxsize=self.queue_size),
            world_id=world_id,
            command_id=command_id,
        )
        with self._lock:
            self._subscriptions[subscription.id] = subscription
        return subscription

    def unsubscribe(self, subscription: TraceSubscription) -> None:
        with self._lock:
            self._subscriptions.pop(subscription.id, None)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscriptions)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscriptions = tuple(self._subscriptions.values())
        for subscription in subscriptions:
            if subscription.world_id is not None and (
                event.get("world_id") != subscription.world_id
            ):
                continue
            if subscription.command_id is not None and (
                event.get("command_id") != subscription.command_id
            ):
                continue
            try:
                subscription.loop.call_soon_threadsafe(
                    self._enqueue, subscription, dict(event)
                )
            except RuntimeError:
                self.unsubscribe(subscription)

    @staticmethod
    def _enqueue(subscription: TraceSubscription, event: dict[str, Any]) -> None:
        if subscription.queue.full():
            while not subscription.queue.empty():
                subscription.queue.get_nowait()
            subscription.queue.put_nowait(
                trace_stream_event(
                    "stream.gap",
                    command_id=event.get("command_id"),
                    world_id=event.get("world_id"),
                    provisional=False,
                    data={"reason": "client_queue_overflow"},
                )
            )
        subscription.queue.put_nowait(event)


trace_event_broker = TraceEventBroker()
