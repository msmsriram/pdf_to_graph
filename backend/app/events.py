"""In-process pub/sub event bus for live processing updates.

Each document has:
  - a replay buffer (every event emitted so far), so a WebSocket that connects
    *after* processing started is caught up instantly, then streamed live.
  - a set of subscriber queues (one per connected WebSocket).

Everything runs in a single asyncio loop, so subscribe()+get_backlog() with no
await between them is atomic (no lost/duplicated events).
"""
from __future__ import annotations

import asyncio
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._backlog: dict[str, list[dict[str, Any]]] = {}

    def get_backlog(self, document_id: str) -> list[dict[str, Any]]:
        return list(self._backlog.get(document_id, []))

    def subscribe(self, document_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(document_id, []).append(q)
        return q

    def unsubscribe(self, document_id: str, q: asyncio.Queue) -> None:
        subs = self._subscribers.get(document_id)
        if subs and q in subs:
            subs.remove(q)
        if subs is not None and not subs:
            self._subscribers.pop(document_id, None)

    async def publish(self, document_id: str, event: dict[str, Any]) -> None:
        self._backlog.setdefault(document_id, []).append(event)
        for q in list(self._subscribers.get(document_id, [])):
            q.put_nowait(event)
        # Yield control so the WebSocket sender tasks can flush promptly.
        await asyncio.sleep(0)

    def clear(self, document_id: str) -> None:
        self._backlog.pop(document_id, None)
        self._subscribers.pop(document_id, None)


# Single shared instance for the app.
bus = EventBus()
