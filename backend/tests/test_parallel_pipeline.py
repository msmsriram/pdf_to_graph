import asyncio
import copy
import tempfile
import threading
import time
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import pipeline, storage, costs
from app.config import get_settings
from app.events import EventBus
from app.schemas import ExtractedChart, GateResult, PageExtraction
from app import gate


class ParallelPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='parallel-chart-test-')
        self.settings = get_settings()
        self.patches = [patch.object(self.settings, key, value) for key, value in {
            'storage_backend': 'local', 'storage_dir': self.temp.name, 'page_batch_size': 5,
            'llm_concurrency': 5, 'gate_concurrency': 5, 'extraction_concurrency': 5,
            'gate_enabled': True, 'gate_images': True, 'openai_model': 'gpt-6.1-sol'}.items()]
        self.events = EventBus()
        self.patches += [patch.object(pipeline, 'bus', self.events), patch.object(pipeline.style_metrics, 'measure', return_value=None)]
        for p in self.patches:
            p.start()
        sources = []
        for number in range(1, 8):
            path = storage.doc_dir('parallel') / 'sources' / f'image{number}.png'
            path.parent.mkdir(exist_ok=True)
            Image.new('RGB', (32, 32), 'white').save(path)
            sources.append({'kind': 'image', 'filename': path.name, 'label': f'Page {number}'})
        storage.create_manifest('parallel', 'Parallel', 'images', sources)
        self.lock = threading.Lock()
        self.active = self.peak = self.gates = self.extracts = self.gate_peak = self.extract_peak = 0
        self.starts = {}
        self.finishes = {}

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def enter(self, stage, number):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            name = 'gates' if stage == 'gate' else 'extracts'
            setattr(self, name, getattr(self, name) + 1)
            peak = 'gate_peak' if stage == 'gate' else 'extract_peak'
            setattr(self, peak, max(getattr(self, peak), getattr(self, name)))
            self.starts[(stage, number)] = time.perf_counter()

    def leave(self, stage, number):
        with self.lock:
            self.active -= 1
            name = 'gates' if stage == 'gate' else 'extracts'
            setattr(self, name, getattr(self, name) - 1)
            self.finishes[(stage, number)] = time.perf_counter()

    def gate(self, path):
        n = int(path.stem.split('_')[1])
        self.enter('gate', n)
        time.sleep(0.005 if n == 4 else 0.025)
        self.leave('gate', n)
        return GateResult(has_charts=True, chart_count=1, confidence=0.99, reason='Test chart'), self.usage(self.settings.gate_model)

    def extract(self, path, number, context):
        self.enter('extract', number)
        time.sleep(0.04 if number == 4 else (0.5 if number == 1 else 0.3))
        self.leave('extract', number)
        chart = ExtractedChart(title=f'Chart {number}', bbox=[0, 0, 1, 1])
        return PageExtraction(page_number=number, summary='test', has_charts=True, charts=[chart], updated_running_notes=''), self.usage(self.settings.openai_model)

    @staticmethod
    def usage(model):
        return {'model': model, 'effort': 'high', 'input_tokens': 10, 'output_tokens': 20,
                'cached_tokens': 0, 'reasoning_tokens': 0, 'total_tokens': 30}

    def test_parallel_stages_batch_boundaries_and_ordered_persistence(self):
        with patch.object(pipeline.gate, 'classify_page', side_effect=self.gate), patch.object(pipeline, 'extract_page', side_effect=self.extract):
            asyncio.run(pipeline.process_document('parallel'))
        m = storage.read_manifest('parallel')
        self.assertEqual(m['status'], 'complete', m.get('error'))
        self.assertEqual([c['page_number'] for c in m['charts']], list(range(1, 8)))
        self.assertEqual(m['progress']['pages_analyzed'], 7)
        self.assertEqual(m['usage']['totals']['calls'], 7)
        self.assertEqual(m['usage']['gate']['totals']['calls'], 7)
        self.assertLessEqual(self.peak, 5)
        self.assertGreater(self.gate_peak, 1)
        self.assertGreater(self.extract_peak, 1)
        self.assertGreaterEqual(self.starts[('gate', 6)], max(t for (stage, n), t in self.finishes.items() if stage == 'extract' and n <= 5))
        arrived = [e['chart']['page_number'] for e in self.events.get_backlog('parallel') if e['type'] == 'chart_extracted']
        self.assertEqual(arrived[0], 4)
        self.assertNotEqual(arrived, sorted(arrived))

    def test_one_failed_page_keeps_successful_siblings(self):
        def extract(path, number, context):
            if number == 2:
                raise RuntimeError('intentional page failure')
            return self.extract(path, number, context)
        with patch.object(pipeline.gate, 'classify_page', side_effect=self.gate), patch.object(pipeline, 'extract_page', side_effect=extract):
            asyncio.run(pipeline.process_document('parallel'))
        m = storage.read_manifest('parallel')
        self.assertEqual(m['status'], 'error')
        self.assertEqual(len(m['charts']), 6)
        self.assertEqual(m['progress']['pages_analyzed'], 7)
        self.assertEqual(m['pages'][1]['status'], 'error')

    def test_cancelled_model_threads_keep_slots_until_they_finish(self):
        release = threading.Event()
        entered = threading.Event()
        count = 0

        def blocked(path):
            nonlocal count
            with self.lock:
                count += 1
                if count == 5:
                    entered.set()
            release.wait(5)
            return GateResult(has_charts=True, chart_count=1, confidence=0.99, reason='test'), self.usage(self.settings.gate_model)

        async def run():
            pipeline.start('parallel')
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(0.001)
            self.assertTrue(entered.is_set())
            await pipeline.cancel('parallel')
            extra = asyncio.create_task(pipeline._model_call('gate', lambda: 'next'))
            await asyncio.sleep(0.01)
            self.assertFalse(extra.done())
            release.set()
            self.assertEqual(await extra, 'next')
            while pipeline._inflight:
                await asyncio.sleep(0.001)

        try:
            with patch.object(pipeline.gate, 'classify_page', side_effect=blocked):
                asyncio.run(run())
        finally:
            release.set()
        m = storage.read_manifest('parallel')
        self.assertEqual(m['status'], 'cancelled')
        self.assertEqual(m['charts'], [])


class EventAndPricingTests(unittest.TestCase):
    def test_sdk_timeout_retry_is_visible_in_cost_completeness(self):
        import httpx
        import json
        from openai import OpenAI
        from app.openai_client import _parse_with_headers, _usage_from
        attempts = []
        def respond(request):
            attempts.append(request.headers.get('x-stainless-retry-count'))
            if len(attempts) == 1:
                raise httpx.ReadTimeout('simulated lost response', request=request)
            return httpx.Response(200, json={
                'id': 'resp_test', 'object': 'response', 'created_at': 0, 'status': 'completed', 'model': 'gpt-5.4-mini',
                'output': [{'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                            'content': [{'type': 'output_text', 'text': json.dumps({'has_charts': True, 'chart_count': 1, 'confidence': 0.9, 'reason': 'test'}), 'annotations': []}]}],
                'usage': {'input_tokens': 10, 'output_tokens': 20, 'total_tokens': 30,
                          'input_tokens_details': {'cached_tokens': 0}, 'output_tokens_details': {'reasoning_tokens': 0}}})
        with OpenAI(api_key='test-only', max_retries=1, http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client:
            response, _ = _parse_with_headers(client, model='gpt-5.4-mini', input='test', text_format=GateResult)
        self.assertTrue(response.output_parsed.has_charts)
        usage = _usage_from(response, 'gpt-5.4-mini', 'low')
        self.assertEqual(usage['sdk_retry_count'], 1)
        self.assertFalse(usage['billing_complete'])
        self.assertEqual(attempts, ['0', '1'])

    def test_gate_failure_is_uncertainty_and_does_not_skip_the_page(self):
        fallback = GateResult(has_charts=True, chart_count=0, confidence=0.0, reason='Gate error')
        self.assertGreaterEqual(gate.chart_probability(fallback), get_settings().gate_threshold)
        self.assertIsNone(costs.cost_usd(gate._zero_usage('gpt-5.4-mini', 'low'), costs.pricing_for('gpt-5.4-mini')))

    def test_replay_preserves_unique_monotonic_event_ids(self):
        events = EventBus()
        async def publish():
            await events.publish('doc', {'type': 'log', 'message': 'one'})
            await events.publish('doc', {'type': 'log', 'message': 'two'})
        asyncio.run(publish())
        backlog = events.get_backlog('doc')
        self.assertEqual([e['sequence'] for e in backlog], [1, 2])
        self.assertNotEqual(backlog[0]['event_id'], backlog[1]['event_id'])
        self.assertEqual(events.get_backlog('doc')[0]['event_id'], backlog[0]['event_id'])

    def test_unknown_prices_are_not_zero_and_sol_prices_are_known(self):
        usage = {'input_tokens': 1000, 'cached_tokens': 100, 'output_tokens': 200}
        self.assertIsNone(costs.cost_usd(usage, costs.pricing_for('unknown-model')))
        self.assertAlmostEqual(costs.cost_usd(usage, costs.pricing_for('gpt-6.1-sol')), 0.00381)

    def test_historical_unknown_price_can_be_recovered_from_saved_usage(self):
        usage = {'model': 'gpt-6.1-sol', 'input_tokens': 1000, 'output_tokens': 200, 'cost_usd': 0}
        m = {'pages': [{'usage': copy.deepcopy(usage)}], 'usage': {'model': 'gpt-6.1-sol', 'pricing': {'known': False}, 'totals': {}, 'reruns': []}}
        self.assertTrue(costs.refresh_pricing(m))
        self.assertAlmostEqual(m['pages'][0]['usage']['cost_usd'], 0.004)
        self.assertAlmostEqual(m['usage']['totals']['cost_usd'], 0.004)
