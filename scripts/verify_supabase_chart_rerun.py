"""Check chart-only re-extraction from an empty cache without a billed call."""
import copy
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app import main, storage
from app.config import get_settings
from app.schemas import ExtractedChart


def run():
    settings = get_settings()
    root = settings.storage_path
    source = storage.read_manifest('repro_after')
    new_id = 'rerun_test_' + uuid4().hex[:12]
    m = {k: copy.deepcopy(v) for k, v in source.items() if k not in ('_storage_revision', '_storage_assets')}
    m['document_id'] = new_id
    m['name'] = 'Chart re-extraction verification'
    m['created_at'] = m['updated_at'] = storage._now()
    chart = m['charts'][0]
    payload = storage.asset_path('repro_after', chart['crop_image']).read_bytes()
    restored = []

    def fake_single(path, prior, effort, model):
        assert path.read_bytes() == payload
        with Image.open(path) as image:
            image.load()
        restored.append(True)
        spec = ExtractedChart.model_validate({**prior, 'bbox': [0, 0, 1, 1]})
        return spec, {'model': settings.openai_model, 'effort': effort or 'high', 'input_tokens': 0,
                      'cached_tokens': 0, 'output_tokens': 0, 'reasoning_tokens': 0, 'total_tokens': 0}

    with tempfile.TemporaryDirectory(prefix='chart-rerun-test-') as cache, patch.object(settings, 'storage_dir', cache):
        target = storage.doc_dir(new_id) / chart['crop_image']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        storage.write_manifest(m)
        try:
            settings.storage_dir = str(Path(cache) / 'empty-instance')
            with TestClient(main.app) as client, patch.object(main, 'extract_single_chart', side_effect=fake_single):
                response = client.post(f'/api/documents/{new_id}/charts/{chart["chart_id"]}/rerun', json={'effort': 'high'})
                assert response.status_code == 200, response.text
                assert restored == [True]
                updated = storage.read_manifest(new_id)
                assert updated['charts'][0]['versions'][-1]['kind'] == 'rerun'
                assert len(updated['usage']['reruns']) == 1
                assert updated['usage']['totals']['calls'] == 1
                assert response.json()['current_version'] == len(chart['versions'])
                report = {'passed': True, 'crop_restored_from_cloud': True,
                          'crop_bytes_identical': True, 'crop_decoded': True,
                          'version_and_usage_persisted': True, 'actual_model_calls': 0,
                          'model_response_mocked': True}
                (root / 'supabase-proof/chart-rerun-proof.json').write_text(json.dumps(report, indent=2))
                print(json.dumps(report, indent=2))
        finally:
            storage.delete_document(new_id)


if __name__ == '__main__':
    run()
