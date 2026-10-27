"""One real PDF upload, real screening/extraction and real Supabase persistence.

Uses the existing three-page chart PDF. Retains the resulting document for human
inspection. Requires the backend's OPENAI_API_KEY; makes billable model calls.
No second sequential run is performed. Never prints keys or HTTP auth headers.
"""
import asyncio
import io
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile

ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, str(ROOT / 'backend'))
from app import gate, main, openai_client, pipeline, storage
from app.config import get_settings


async def run():
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError('OPENAI_API_KEY is missing from backend configuration')
    # A free model-access check avoids uploading if the key/model is rejected.
    await asyncio.to_thread(openai_client._get_client().models.retrieve, settings.openai_model)
    source = await asyncio.to_thread(storage.read_manifest, '62e42814f034')
    if source:
        original = await asyncio.to_thread(storage.asset_path, source['document_id'], 'original.pdf')
    else:
        # A user may have deleted the old demo card; upload the remaining local
        # original under a new id, without recreating that deleted cloud record.
        folder = ROOT / 'backend/storage/62e42814f034'
        source = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        original = folder / 'original.pdf'
    original_parse = openai_client._parse_with_headers
    mutex = threading.Lock()
    active = peak = 0
    stage_active = {'gate': 0, 'extract': 0}
    stage_peak = {'gate': 0, 'extract': 0}
    calls = []
    started = time.perf_counter()

    def trace(scope):
        def call(client, **kwargs):
            nonlocal active, peak
            began = time.perf_counter()
            with mutex:
                active += 1
                stage_active[scope] += 1
                peak = max(peak, active)
                stage_peak[scope] = max(stage_peak[scope], stage_active[scope])
            try:
                response, limits = original_parse(client, **kwargs)
                with mutex:
                    calls.append({'scope': scope, 'model': kwargs['model'], 'seconds': round(time.perf_counter() - began, 2),
                                  'started_seconds': round(began - started, 2), 'rate_limits': limits,
                                  'response_id': getattr(response, 'id', None)})
                return response, limits
            finally:
                with mutex:
                    active -= 1
                    stage_active[scope] -= 1
        return call

    with patch.object(openai_client, '_parse_with_headers', side_effect=trace('extract')), \
         patch.object(gate, '_parse_with_headers', side_effect=trace('gate')):
        result = await main.upload_document(files=[UploadFile(file=io.BytesIO(original.read_bytes()), filename=source['original_filename'])],
            name='Live parallel verification · ' + source['name'], image_labels='[]')
        document_id = result['document_id']
        print('DOCUMENT_ID=' + document_id, flush=True)
        task = pipeline._tasks[document_id]
        last = 0
        async def monitor():
            nonlocal last
            while not task.done():
                events = pipeline.bus.get_backlog(document_id)
                for event in events:
                    if event['sequence'] <= last:
                        continue
                    last = event['sequence']
                    if event['type'] in {'status', 'log', 'error'}:
                        print(event.get('message', ''), flush=True)
                await asyncio.sleep(1)
        watcher = asyncio.create_task(monitor())
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=900)
        finally:
            if not task.done():
                await pipeline.cancel(document_id)
            await asyncio.gather(watcher, return_exceptions=True)
    m = await asyncio.to_thread(storage.read_manifest, document_id)
    order = [e['page_number'] for e in pipeline.bus.get_backlog(document_id) if e['type'] == 'page_analyzed']
    report = {'document_id': document_id, 'model': settings.openai_model, 'gate_model': settings.gate_model,
              'status': m['status'], 'error': m.get('error'), 'page_count': m['page_count'], 'charts': m['charts_count'],
              'actual_peak_concurrent_calls': peak, 'actual_peak_by_stage': stage_peak,
              'wall_seconds': round(time.perf_counter() - started, 2), 'page_completion_order': order,
              'stored_chart_page_order': [c['page_number'] for c in m['charts']], 'calls': calls,
              'pages': [{'number': p['page_number'], 'status': p['status'], 'charts': len(p['chart_ids']),
                         'gate_cost_usd': (p.get('gate') or {}).get('usage', {}).get('cost_usd'),
                         'extraction_cost_usd': (p.get('usage') or {}).get('cost_usd')} for p in m['pages']],
              'usage': m.get('usage'), 'verified_at': datetime.now(timezone.utc).isoformat(),
              'sequential_comparison_run': False, 'actual_model_calls': True}
    output = ROOT / 'backend/storage/supabase-proof/live-parallel-proof.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k not in ('usage', 'calls')}, indent=2), flush=True)
    if m['status'] != 'complete':
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(run())
