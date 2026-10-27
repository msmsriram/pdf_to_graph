"""Provision app table/private bucket and optionally import existing local documents.

Uses the local PostgreSQL test driver; runtime app uses only HTTPS APIs.
Run: python scripts/setup_supabase_storage.py [--import-local]
"""
import json
import sys
from pathlib import Path

import psycopg

from supabase_probe import ENV, ROOT

sys.path.insert(0, str(ROOT / 'backend'))
from app import storage, supabase_storage as remote
from app.config import get_settings


def main():
    with psycopg.connect(host=ENV['SUPABASE_POOLER_HOST'], port=5432, dbname='postgres',
                         user=ENV['SUPABASE_POOLER_USER'], password=ENV['SUPABASE_DB_PASSWORD'],
                         sslmode='require', connect_timeout=20) as conn:
        conn.execute((ROOT / 'backend/supabase_schema.sql').read_text())
    settings = get_settings()
    buckets = remote.request('GET', '/storage/v1/bucket')
    if not any(b['id'] == settings.supabase_bucket for b in buckets):
        remote.request('POST', '/storage/v1/bucket', {'id': settings.supabase_bucket, 'name': settings.supabase_bucket, 'public': False})
    bucket = remote.request('GET', '/storage/v1/bucket/' + settings.supabase_bucket)
    assert bucket['public'] is False
    print('Application table and private bucket ready', flush=True)
    if '--import-local' in sys.argv:
        for path in sorted(settings.storage_path.glob('*/manifest.json')):
            manifest = json.loads(path.read_text(encoding='utf-8'))
            if remote.get_record(manifest['document_id']):
                print('Already imported: ' + manifest['document_id'], flush=True)
                continue
            manifest.pop('_storage_revision', None)
            manifest.pop('_storage_assets', None)
            # Preserve incomplete legacy fixtures but make missing originals explicit.
            manifest['source_files_available'] = bool(manifest.get('sources')) and all(
                (path.parent / ('original.pdf' if s['kind'] == 'pdf' else 'sources/' + s['filename'])).is_file()
                for s in manifest.get('sources', []))
            storage.write_manifest(manifest)
            print('Imported: ' + manifest['document_id'], flush=True)


if __name__ == '__main__':
    main()
