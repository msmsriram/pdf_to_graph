import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import main, storage
from app.events import EventBus
from app.config import get_settings


class WebSocketReplayTests(unittest.TestCase):
    def test_reconnect_sends_only_events_after_cursor(self):
        events = EventBus()
        async def emit():
            await events.publish('doc', {'document_id': 'doc', 'type': 'log', 'message': 'one'})
            await events.publish('doc', {'document_id': 'doc', 'type': 'log', 'message': 'two'})
        asyncio.run(emit())
        with patch.object(main, 'bus', events):
            client = TestClient(main.app)  # No lifespan: this test does not touch cloud jobs.
            with client.websocket_connect('/ws/documents/doc') as ws:
                self.assertEqual(ws.receive_json()['sequence'], 1)
                self.assertEqual(ws.receive_json()['sequence'], 2)
            with client.websocket_connect(f'/ws/documents/doc?stream={events.stream_id}&after=1') as ws:
                self.assertEqual(ws.receive_json()['sequence'], 2)

    def test_empty_event_stream_restores_persisted_snapshot(self):
        settings = get_settings()
        with tempfile.TemporaryDirectory(prefix='ws-snapshot-') as cache, \
             patch.object(settings, 'storage_backend', 'local'), patch.object(settings, 'storage_dir', cache), \
             patch.object(main, 'bus', EventBus()):
            storage.create_manifest('doc', 'Saved document', 'file.pdf')
            storage.set_status('doc', 'complete', 'Saved')
            client = TestClient(main.app)
            with client.websocket_connect('/ws/documents/doc') as ws:
                snapshot = ws.receive_json()
                self.assertEqual(snapshot['type'], 'snapshot')
                self.assertEqual(snapshot['document']['status'], 'complete')
