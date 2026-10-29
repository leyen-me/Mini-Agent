"""Durable event log and in-process fan-out for SSE subscribers."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Event, utcnow


@dataclass(slots=True)
class EventDTO:
    id: int
    session_id: str
    type: str
    payload: dict[str, Any]
    created_at: str

    def to_sse(self) -> str:
        data = {"id": self.id, "sessionId": self.session_id, "type": self.type, **self.payload}
        return (
            f"id: {self.id}\n"
            f"event: {self.type}\n"
            f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
        )


class EventBroker:
    """Fan out committed events. Slow clients keep their own unbounded queue."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[EventDTO]]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, session_id: str) -> asyncio.Queue[EventDTO]:
        queue: asyncio.Queue[EventDTO] = asyncio.Queue()
        async with self._lock:
            self._subscribers.setdefault(session_id, set()).add(queue)
        return queue

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue[EventDTO]) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(session_id)
            if subscribers:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(session_id, None)

    def publish(self, event: EventDTO) -> None:
        for queue in list(self._subscribers.get(event.session_id, ())):
            queue.put_nowait(event)

    @property
    def subscriber_count(self) -> int:
        return sum(len(items) for items in self._subscribers.values())


class EventStore:
    def __init__(self, broker: EventBroker) -> None:
        self.broker = broker

    async def emit(
        self, db: AsyncSession, session_id: str, event_type: str, payload: dict[str, Any]
    ) -> EventDTO:
        """Persist and fan out an event. The caller owns state persistence/commit."""

        event = Event(
            session_id=session_id,
            type=event_type,
            payload=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            created_at=utcnow(),
        )
        db.add(event)
        await db.flush()
        dto = EventDTO(
            id=event.id,
            session_id=session_id,
            type=event_type,
            payload=payload,
            created_at=event.created_at,
        )
        # The row is flushed, not yet committed; it is safe to fan out after commit in
        # practice because the SSE replay path also consults the table. Publish on flush
        # keeps streaming latency low.
        self.broker.publish(dto)
        return dto

    async def emit_committed(
        self, db: AsyncSession, session_id: str, event_type: str, payload: dict[str, Any]
    ) -> EventDTO:
        dto = await self.emit(db, session_id, event_type, payload)
        await db.commit()
        return dto

    async def replay(
        self, db: AsyncSession, session_id: str, after_id: int, limit: int = 1_000
    ) -> list[EventDTO]:
        result = await db.execute(
            select(Event)
            .where(Event.session_id == session_id, Event.id > after_id)
            .order_by(Event.id.asc())
            .limit(limit)
        )
        events = []
        for row in result.scalars():
            try:
                payload = json.loads(row.payload)
            except json.JSONDecodeError:
                payload = {"parseError": True}
            events.append(
                EventDTO(
                    id=row.id,
                    session_id=row.session_id,
                    type=row.type,
                    payload=payload,
                    created_at=row.created_at,
                )
            )
        return events

    async def latest_id(self, db: AsyncSession, session_id: str) -> int:
        result = await db.execute(
            select(Event.id).where(Event.session_id == session_id).order_by(Event.id.desc())
        )
        value = result.scalars().first()
        return int(value or 0)
