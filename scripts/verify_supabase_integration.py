"""Live integration proof with a disposable cache and mocked model responses.

Uses real Supabase files/database and real FastAPI endpoints. No billed model call.
Only records created by this script are removed in cleanup.
"""
import asyncio
import base64
import copy
import hashlib
import io
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import fitz
from PIL import Image
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app import main, pipeline, storage, supabase_storage as remote
from app.config import get_settings
from app.openai_client import _encode_image
from app.schemas import ExtractedChart, PageExtraction


def main_test():
    settings = get_settings()
    original_root = settings.storage_path
    pdf_bytes = (original_root / '62e42814f034/original.pdf').read_bytes()
    png_bytes = (original_root / 'repro_after/charts/p003_c01.png').read_bytes()
    fixture = json.loads((original_root / 'repro_after/manifest.json').read_text())['charts'][0]
    extracted = ExtractedChart.model_validate({**fixture['versions'][0]['spec'], 'bbox': [0, 0, 1, 1]})
    report = {'checks': [], 'actual_model_calls': 0, 'model_responses_mocked': True}
    created = []

    def passed(label):
        report['checks'].append(label)
        print('PASS ' + label, flush=True)

    def fake_extract(path, page_number, notes):
        with Image.open(path) as im:
            im.load()
            assert im.width > 0 and im.height > 0
        encoded = _encode_image(path)
        assert base64.b64decode(encoded.split(',', 1)[1]) == path.read_bytes()
        return PageExtraction(page_number=page_number, summary='Integration verification fixture',
                              has_charts=True, charts=[extracted], updated_running_notes=''), {
            'model': settings.openai_model, 'effort': settings.reasoning_effort,
            'input_tokens': 0, 'output_tokens': 0, 'cached_tokens': 0, 'reasoning_tokens': 0, 'total_tokens': 0}

    with tempfile.TemporaryDirectory(prefix='chart-studio-cloud-test-') as cache:
        with patch.object(settings, 'storage_dir', str(Path(cache) / 'instance-a')), \
             patch.object(settings, 'openai_api_key', 'test-only-never-sent'), \
             patch.object(settings, 'gate_enabled', False), \
             patch.object(settings, 'max_pages', 1), patch.object(settings, 'render_dpi', 72), \
             patch.object(pipeline, 'start') as start, patch.object(pipeline, 'extract_page', side_effect=fake_extract):
            try:
                with TestClient(main.app) as client:
                    assert client.get('/api/health').json()['storage_backend'] == 'supabase'
                    assert len(client.get('/api/documents').json()) >= 4
                    passed('Cloud homepage history available with an empty local cache')
                    for doc_id, rel, source in [('62e42814f034', 'original.pdf', pdf_bytes),
                                                ('repro_after', 'charts/p003_c01.png', png_bytes)]:
                        response = client.get('/storage/' + doc_id + '/' + rel)
                        assert response.status_code == 200 and response.content == source
                    passed('Original PDF and chart image restored byte-for-byte through application URLs')

                    response = client.post('/api/documents', data={'name': 'Supabase integration test'}, files=[
                        ('files', ('original.pdf', pdf_bytes, 'application/pdf')),
                        ('files', ('chart.png', png_bytes, 'image/png'))])
                    assert response.status_code == 200, response.text
                    original_id = response.json()['document_id']
                    created.append(original_id)
                    start.assert_called_with(original_id)
                    storage.set_status(original_id, 'complete', 'Verification upload; no model calls')
                    passed('Real upload endpoint persists PDF and uploaded-image originals')

                    settings.storage_dir = str(Path(cache) / 'instance-b')
                    response = client.get('/storage/' + original_id + '/sources/img_001.png')
                    assert response.status_code == 200 and response.content == png_bytes
                    with Image.open(io.BytesIO(response.content)) as image:
                        image.load()
                    response = client.post('/api/documents/' + original_id + '/reprocess')
                    assert response.status_code == 200, response.text
                    child = response.json()['document_id']
                    created.append(child)
                    assert storage.asset_path(child, 'original.pdf').read_bytes() == pdf_bytes
                    assert storage.asset_path(child, 'sources/img_001.png').read_bytes() == png_bytes
                    passed('Reprocess endpoint restores exact PDF/image originals after cache loss and creates a separate run')
                    asyncio.run(pipeline.process_document(child))
                    doc = storage.read_manifest(child)
                    assert doc['status'] == 'complete', doc.get('error')
                    assert doc['original_pdf_page_count'] == 3 and doc['page_count'] == 2
                    assert len(doc['charts']) == 2
                    passed('Real render/crop/persistence pipeline completes using restored sources and mocked extraction')

                    cid = doc['charts'][0]['chart_id']
                    spec = copy.deepcopy(doc['charts'][0]['versions'][0]['spec'])
                    spec['title'] = 'Saved cloud edit'
                    response = client.post(f'/api/documents/{child}/charts/{cid}/versions', json={'spec': spec})
                    assert response.status_code == 200, response.text
                    response = client.post(f'/api/documents/{child}/charts/{cid}/finalize')
                    assert response.status_code == 200
                    response = client.post(f'/api/documents/{child}/charts/{cid}/revert')
                    assert response.status_code == 200 and response.json()['current_version'] == 0
                    summary = next(d for d in client.get('/api/documents').json() if d['document_id'] == child)
                    assert summary['finalized_count'] == 0
                    response = client.post(f'/api/documents/{child}/charts/{cid}/set-version/1')
                    assert response.status_code == 200
                    passed('Save, finalize, revert and version selection persist; homepage counts follow the selected version')

                    settings.storage_dir = str(Path(cache) / 'instance-c')
                    result = client.get('/api/documents/' + child)
                    assert result.status_code == 200, result.text
                    recovered = result.json()
                    assert recovered['charts'][0]['versions'][1]['spec']['title'] == 'Saved cloud edit'
                    crop = client.get('/storage/' + child + '/' + recovered['charts'][0]['crop_image'])
                    assert crop.status_code == 200
                    with Image.open(io.BytesIO(crop.content)) as image:
                        image.load()
                    with fitz.open(stream=client.get('/storage/' + child + '/original.pdf').content, filetype='pdf') as pdf:
                        assert len(pdf) == 3
                    passed('Versions, original PDF and generated chart crop survive a second empty-cache instance')

                    first = storage.read_manifest(child)
                    stale = storage.read_manifest(child)
                    first['status_message'] = 'Concurrent update verification'
                    storage.write_manifest(first)
                    stale['status_message'] = 'This stale write must fail'
                    try:
                        storage.write_manifest(stale)
                        raise AssertionError('Stale update was accepted')
                    except remote.StorageConflict:
                        pass
                    passed('Optimistic revisions reject stale writes instead of overwriting newer results')
                    try:
                        storage.asset_path(child, '../config.py')
                        raise AssertionError('Path traversal accepted')
                    except ValueError:
                        pass
                    passed('Asset paths cannot escape their document cache')

                    response = client.post('/api/documents', files={'files': ('chart.png', png_bytes, 'image/png')})
                    assert response.status_code == 200
                    image_id = response.json()['document_id']
                    created.append(image_id)
                    # A restart must mark abandoned jobs clearly instead of leaving them spinning.
                    storage.mark_interrupted()
                    assert storage.read_manifest(image_id)['status'] == 'error'
                    settings.storage_dir = str(Path(cache) / 'instance-d')
                    response = client.post('/api/documents/' + image_id + '/reprocess')
                    assert response.status_code == 200, response.text
                    image_child = response.json()['document_id']
                    created.append(image_child)
                    assert storage.asset_path(image_child, 'sources/img_001.png').read_bytes() == png_bytes
                    passed('Image-only uploads also reprocess from cloud; interrupted runs are marked recoverable')
                    report['cleanup'] = []
                    for doc_id in reversed(created):
                        response = client.delete('/api/documents/' + doc_id)
                        assert response.status_code == 200, response.text
                        assert remote.get_record(doc_id) is None
                        assert remote.request('POST', '/storage/v1/object/list/' + settings.supabase_bucket,
                                              {'prefix': doc_id, 'limit': 1}) == []
                        report['cleanup'].append(doc_id)
                    created.clear()
                    passed('Deleting test documents removes both database records and remote file objects')
            finally:
                for doc_id in created:
                    storage.delete_document(doc_id)

    report['passed'] = True
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    (original_root / 'supabase-proof/integration-proof.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main_test()
