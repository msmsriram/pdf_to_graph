"""In-process lifecycle and cooperative cancellation for model-backed jobs."""
from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


class ProcessingCancelled(Exception):
    """Raised inside a worker when the user has stopped its active job."""


@dataclass
class JobControl:
    job_id: str
    document_id: str
    scope: str
    chart_id: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    task: asyncio.Task[Any] | None = None
    status: str = "running"
    _stream_closer: Callable[[], None] | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def check(self) -> None:
        if self.cancel_event.is_set():
            raise ProcessingCancelled("Stopped by user")

    def set_stream_closer(self, closer: Callable[[], None] | None) -> None:
        """Expose the active HTTP stream so the cancel endpoint can interrupt it."""
        with self._lock:
            self._stream_closer = closer
            should_close = closer is not None and self.cancel_event.is_set()
        if should_close:
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass

    def request_cancel(self) -> bool:
        with self._lock:
            if self.status not in {"running", "cancelling"}:
                return False
            self.status = "cancelling"
            self.cancel_event.set()
            closer = self._stream_closer
        if closer:
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass
        return True

    def commit_if_active(self, callback: Callable[[], Any]) -> Any:
        """Make cancellation and the final durable commit mutually exclusive."""
        with self._lock:
            self.check()
            return callback()


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, JobControl] = {}
        self._active: dict[tuple[str, str, str | None], str] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _key(document_id: str, scope: str, chart_id: str | None) -> tuple[str, str, str | None]:
        # Serialize chart re-extractions within one document because each one
        # performs a read/modify/write of the same manifest and usage totals.
        return (document_id, scope, None if scope == "chart" else chart_id)

    def create(self, document_id: str, scope: str, chart_id: str | None = None) -> JobControl:
        key = self._key(document_id, scope, chart_id)
        with self._lock:
            prior_id = self._active.get(key)
            prior = self._jobs.get(prior_id or "")
            if prior and prior.status in {"running", "cancelling"}:
                raise RuntimeError("A matching job is already active.")
            control = JobControl(uuid.uuid4().hex[:12], document_id, scope, chart_id)
            self._jobs[control.job_id] = control
            self._active[key] = control.job_id
            return control

    def attach(self, control: JobControl, task: asyncio.Task[Any]) -> None:
        control.task = task

    def get(self, job_id: str) -> JobControl | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_active(self, document_id: str) -> list[dict[str, str | None]]:
        """Return reconnect metadata without exposing tasks or cancellation primitives."""
        with self._lock:
            active_ids = set(self._active.values())
            return [
                {
                    "job_id": control.job_id,
                    "scope": control.scope,
                    "chart_id": control.chart_id,
                    "status": control.status,
                }
                for job_id, control in self._jobs.items()
                if job_id in active_ids
                and control.document_id == document_id
                and control.status in {"running", "cancelling"}
            ]

    def cancel(self, document_id: str, job_id: str) -> JobControl | None:
        control = self.get(job_id)
        if not control or control.document_id != document_id:
            return None
        control.request_cancel()
        return control

    def finish(self, control: JobControl, status: str) -> None:
        with self._lock:
            control.set_stream_closer(None)
            control.status = status
            key = self._key(control.document_id, control.scope, control.chart_id)
            if self._active.get(key) == control.job_id:
                self._active.pop(key, None)


registry = JobRegistry()
