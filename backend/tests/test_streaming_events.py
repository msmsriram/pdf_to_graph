from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.events import EventBus
from app.jobs import JobControl, JobRegistry, ProcessingCancelled
from app.streaming import StructuredStreamObserver


class EventBusTests(unittest.TestCase):
    def test_events_are_sequenced_and_replayed_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = SimpleNamespace(storage_path=root)
            with patch("app.events.get_settings", return_value=settings):
                first = EventBus()
                asyncio.run(first.publish("doc", {"type": "status", "status": "rendering"}))
                asyncio.run(first.publish("doc", {"type": "progress", "progress": {"pages_rendered": 1}}))

                replayed = EventBus().get_backlog("doc", after_sequence=1)

            self.assertEqual([event["sequence"] for event in replayed], [2])
            self.assertEqual(replayed[0]["type"], "progress")
            journal = (root / "doc" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(journal), 2)
            self.assertEqual(json.loads(journal[0])["document_id"], "doc")


class StructuredStreamObserverTests(unittest.TestCase):
    def test_only_new_complete_points_are_emitted(self) -> None:
        observer = StructuredStreamObserver(page_number=3)
        prefix = (
            '{"summary":"A transfer curve","charts":[{"title":"Gain","chart_type":"line",'
            '"x_axis":{"label":"x"},"y_axis":{"label":"y"},'
            '"series":[{"name":"measured","points":['
        )
        first = observer.observe(prefix + '{"x":0,"y":1}')
        second = observer.observe(prefix + '{"x":0,"y":1},{"x":1,"y":2}')

        first_points = [event for event in first if event["type"] == "points_draft_batch"]
        second_points = [event for event in second if event["type"] == "points_draft_batch"]
        self.assertEqual(first_points[0]["points"], [{"x": 0, "y": 1}])
        self.assertEqual(second_points[0]["points"], [{"x": 1, "y": 2}])
        self.assertEqual(second_points[0]["draft_id"], "p003_draft01")


class JobCancellationTests(unittest.TestCase):
    def test_cancel_closes_stream_and_blocks_final_commit(self) -> None:
        closed: list[bool] = []
        committed: list[bool] = []
        control = JobControl("job1", "doc1", "chart", "chart1")
        control.set_stream_closer(lambda: closed.append(True))

        self.assertTrue(control.request_cancel())
        self.assertEqual(closed, [True])
        with self.assertRaises(ProcessingCancelled):
            control.check()
        with self.assertRaises(ProcessingCancelled):
            control.commit_if_active(lambda: committed.append(True))
        self.assertEqual(committed, [])

    def test_registry_serializes_chart_jobs_within_a_document(self) -> None:
        registry = JobRegistry()
        first = registry.create("doc", "chart", "chart1")
        self.assertEqual(
            registry.list_active("doc"),
            [{"job_id": first.job_id, "scope": "chart", "chart_id": "chart1", "status": "running"}],
        )
        with self.assertRaises(RuntimeError):
            registry.create("doc", "chart", "chart1")
        with self.assertRaises(RuntimeError):
            registry.create("doc", "chart", "chart2")
        registry.finish(first, "completed")
        self.assertEqual(registry.list_active("doc"), [])
        second = registry.create("doc", "chart", "chart1")
        self.assertNotEqual(first.job_id, second.job_id)


class DocumentDetailRouteTests(unittest.TestCase):
    def test_document_detail_includes_reconnect_jobs(self) -> None:
        from app.main import app

        manifest = {"document_id": "doc-route", "status": "complete", "charts": []}
        active = [{"job_id": "job-route", "scope": "chart", "chart_id": "chart-route", "status": "running"}]
        with patch("app.main.storage.read_manifest", return_value=manifest), patch(
            "app.main._ensure_style_metrics", return_value=False
        ), patch("app.main.costs.refresh_pricing", return_value=False), patch(
            "app.main.job_registry.list_active", return_value=active
        ):
            response = TestClient(app).get("/api/documents/doc-route")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["active_jobs"], active)


class ChartRerunLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_events_are_scoped_and_cancel_has_no_commit(self) -> None:
        from app.main import _run_chart_rerun

        control = JobControl("job-live", "doc-live", "chart", "chart-live")

        def fake_extract(*args):
            forward = args[3]
            forward({
                "type": "chart_draft_started", "draft_id": "temporary", "chart_index": 0,
                "title": "Live chart", "chart_type": "line",
            })
            raise ProcessingCancelled("Stopped by user")

        published: list[dict] = []

        async def capture(_document_id, event, **_kwargs):
            published.append(event)

        with patch("app.main.extract_single_chart_stream", side_effect=fake_extract), patch(
            "app.main.bus.publish", new=AsyncMock(side_effect=capture)
        ):
            await _run_chart_rerun(control, Path("unused.png"), {}, 2, "high", None)

        types = [event["type"] for event in published]
        self.assertIn("chart_rerun_draft_started", types)
        self.assertIn("chart_rerun_cancelled", types)
        self.assertEqual(control.status, "cancelled")
        self.assertTrue(all(event.get("chart_id") == "chart-live" for event in published))


if __name__ == "__main__":
    unittest.main()
