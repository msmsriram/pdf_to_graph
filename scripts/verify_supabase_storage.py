"""Upload the existing local library to a private proof bucket and verify downloads.

Run with the project's Python environment. Reads backend/.env. Does not import or
change the application. Every object uses a unique proof-run prefix.
"""
import hashlib
import json
import mimetypes
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

from supabase_probe import ENV, ROOT

BUCKET = 'pdf-chart-demo-proof'


def request(method, path, body=None, content_type='application/json', authenticated=True):
    headers = {'Content-Type': content_type}
    if authenticated:
        headers['apikey'] = ENV['SUPABASE_SECRET_KEY']
    if method == 'POST' and path.startswith('/storage/v1/object/' + BUCKET + '/'):
        headers['x-upsert'] = 'true'
    if body is not None and not isinstance(body, bytes):
        body = json.dumps(body, ensure_ascii=False).encode()
    req = Request(ENV['SUPABASE_URL'] + path, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=90) as response:
            return response.status, response.read()
    except HTTPError as exc:
        raise RuntimeError(f'{method} {path}: HTTP {exc.code}: {exc.read().decode()[:400]}') from None


def main():
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8]
    report = {'project_url': ENV['SUPABASE_URL'], 'run_id': run_id, 'bucket': BUCKET,
              'started_at': datetime.now(timezone.utc).isoformat(), 'files': [], 'failures': []}
    if '--resume' in sys.argv:
        report = json.loads((ROOT / 'backend/storage/supabase-proof/latest.json').read_text())
        run_id = report['run_id']
        report.setdefault('recovered_transfer_errors', []).extend(report['failures'])
        report['failures'] = []
    status, body = request('GET', '/storage/v1/bucket')
    buckets = json.loads(body)
    if not any(b['id'] == BUCKET for b in buckets):
        request('POST', '/storage/v1/bucket', {'id': BUCKET, 'name': BUCKET, 'public': False})
    _, body = request('GET', '/storage/v1/bucket/' + BUCKET)
    report['bucket_private'] = not json.loads(body)['public']
    assert report['bucket_private'], 'Proof bucket must be private'

    library = ROOT / 'backend/storage'
    files = sorted(p for p in library.rglob('*') if p.is_file() and 'supabase-proof' not in p.parts)
    for file in files:
        relative = file.relative_to(library).as_posix()
        if any(f['local_relative_path'] == relative and f['verified'] for f in report['files']):
            continue
        key = f'{run_id}/{relative}'
        data = file.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        try:
            path = '/storage/v1/object/' + BUCKET + '/' + quote(key, safe='/')
            upload_status, _ = request('POST', path, data, mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
            download_status, downloaded = request('GET', path)
            actual = hashlib.sha256(downloaded).hexdigest()
            assert actual == digest, 'Downloaded bytes differ'
            entry = {'local_relative_path': relative, 'object_key': key, 'bytes': len(data),
                     'sha256': digest, 'download_sha256': actual, 'upload_status': upload_status,
                     'download_status': download_status, 'verified': True}
            if file.suffix == '.json':
                assert json.loads(downloaded) == json.loads(data)
                entry['json_roundtrip_verified'] = True
            report['files'].append(entry)
            print(f'PASS {relative}: {len(data):,} bytes, SHA-256 matches', flush=True)
        except Exception as exc:
            report['failures'].append({'path': relative, 'error': str(exc)})
            print(f'FAIL {relative}: {exc}', flush=True)

    # Verify a short-lived signed URL, independently of authenticated downloads.
    if report['files']:
        example = next((f for f in report['files'] if f['object_key'].endswith('.pdf')), report['files'][0])
        _, signed_body = request('POST', '/storage/v1/object/sign/' + BUCKET + '/' + quote(example['object_key'], safe='/'), {'expiresIn': 120})
        signed = json.loads(signed_body)
        signed_path = signed.get('signedURL') or signed.get('signedUrl')
        if not signed_path.startswith('/storage/v1/'):
            signed_path = '/storage/v1' + signed_path
        _, downloaded = request('GET', signed_path, authenticated=False)
        report['signed_pdf_download_verified'] = hashlib.sha256(downloaded).hexdigest() == example['sha256']
        assert report['signed_pdf_download_verified']
        # No credentials and no signed token must not reveal a private object.
        try:
            request('GET', '/storage/v1/object/public/' + BUCKET + '/' + quote(example['object_key'], safe='/'), authenticated=False)
            report['anonymous_public_download_blocked'] = False
        except RuntimeError as exc:
            report['anonymous_public_download_blocked'] = 'HTTP 400' in str(exc) or 'HTTP 403' in str(exc) or 'HTTP 404' in str(exc) or 'HTTP 401' in str(exc)

    report['verified_bytes'] = sum(f['bytes'] for f in report['files'])
    report['verified_files'] = len(report['files'])
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    output = library / 'supabase-proof'
    output.mkdir(exist_ok=True)
    proof_path = output / f'{run_id}.json'
    proof_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    (output / 'latest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in ('files',)}, indent=2))
    print('PROOF_FILE=' + str(proof_path))
    if report['failures']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
