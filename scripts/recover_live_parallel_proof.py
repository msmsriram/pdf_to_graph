"""Recover proof using saved state and existing responses only; no model generation."""
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app import storage
from app.openai_client import _get_client


def run():
    document_id = '4c4ef768cd74'
    m = storage.read_manifest(document_id)
    calls = []
    for page in m['pages']:
        for scope, usage in [('gate', (page.get('gate') or {}).get('usage')), ('extract', page.get('usage'))]:
            if not usage or not usage.get('response_id'):
                continue
            response = _get_client().responses.retrieve(usage['response_id'])
            calls.append({'scope': scope, 'page': page['page_number'], 'model': usage['model'],
                          'response_id': usage['response_id'], 'sdk_call_seconds': usage['seconds'],
                          'response_created_at': response.created_at, 'response_completed_at': response.completed_at,
                          'rate_limits': usage.get('rate_limits', {}), 'estimated_cost_usd': usage['cost_usd']})
    start = datetime.fromisoformat(m['processing_config']['started_at'])
    end = datetime.fromisoformat(m['processing_finished_at'])
    wall = (end - start).total_seconds()
    total_model_seconds = sum(c['sdk_call_seconds'] for c in calls)
    order = sorted(m['pages'], key=lambda p: min(c['versions'][0]['created_at'] for c in m['charts'] if c['page_number'] == p['page_number']))
    report = {'document_id': document_id, 'status': m['status'], 'model': m['usage']['model'],
              'page_count': m['page_count'], 'charts': m['charts_count'], 'processing_wall_seconds': round(wall, 2),
              'recorded_model_call_seconds_sum': round(total_model_seconds, 2),
              'minimum_overlapping_page_jobs_from_timings': math.ceil(total_model_seconds / wall),
              'overlap_inference': 'Total recorded call duration / full processing duration gives a conservative lower bound on peak overlapping call jobs; each page has one call at a time.',
              'page_completion_order_from_chart_timestamps': [p['page_number'] for p in order],
              'stored_chart_page_order': [c['page_number'] for c in m['charts']],
              'estimated_recorded_cost_usd': m['usage']['totals']['cost_usd'] + m['usage']['gate']['totals']['cost_usd'],
              'calls': calls, 'pages': [{'page': p['page_number'], 'charts': len(p['chart_ids']),
                    'extraction_cost_usd': p['usage']['cost_usd'], 'gate_cost_usd': p['gate']['usage']['cost_usd']} for p in m['pages']],
              'note': 'The original live logger failed after processing due to console encoding. This report is recovered from durable state and read-only retrieval of existing responses. No second PDF upload or sequential comparison run.',
              'cost_caveat': 'Recorded successful-response estimate; excludes any unreturned or timed-out attempts and is not an invoice.',
              'recovered_at': datetime.now(timezone.utc).isoformat()}
    path = ROOT / 'backend/storage/supabase-proof/live-parallel-proof.json'
    path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    run()
