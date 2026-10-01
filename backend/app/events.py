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
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import get_settings


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._backlog: dict[str, list[dict[str, Any]]] = {}
        self._sequences: dict[str, int] = {}

    def _journal_path(self, document_id: str) -> Path:
        return get_settings().storage_path / document_id / "events.jsonl"

    def _load_journal(self, document_id: str) -> list[dict[str, Any]]:
        """Load a document's durable event history once per process.

        A malformed/truncated final line is ignored so a process interruption cannot
        make the rest of the document unrecoverable.
        """
        if document_id in self._backlog:
            return self._backlog[document_id]
        events: list[dict[str, Any]] = []
        path = self._journal_path(document_id)
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if isinstance(item, dict):
                    events.append(item)
        self._backlog[document_id] = events
        self._sequences[document_id] = max((int(e.get("sequence") or 0) for e in events), default=0)
        return events

    def get_backlog(self, document_id: str, after_sequence: int = 0) -> list[dict[str, Any]]:
        return [e for e in self._load_journal(document_id) if int(e.get("sequence") or 0) > after_sequence]

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

    async def publish(self, document_id: str, event: dict[str, Any], *, persist: bool = True) -> None:
        backlog = self._load_journal(document_id)
        sequence = self._sequences.get(document_id, 0) + 1
        self._sequences[document_id] = sequence
        event = {
            **event,
            "document_id": document_id,
            "sequence": sequence,
            "emitted_at": datetime.now(timezone.utc).isoformat(),
        }
        backlog.append(event)
        if persist:
            path = self._journal_path(document_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        for q in list(self._subscribers.get(document_id, [])):
            q.put_nowait(event)
        # Yield control so the WebSocket sender tasks can flush promptly.
        await asyncio.sleep(0)

    def clear(self, document_id: str) -> None:
        self._backlog.pop(document_id, None)
        self._subscribers.pop(document_id, None)
        self._sequences.pop(document_id, None)


# Single shared instance for the app.
bus = EventBus()
