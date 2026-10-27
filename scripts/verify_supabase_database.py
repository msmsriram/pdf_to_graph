"""Isolated database proof: full manifests + relational chart rows + REST retrieval.

Requires psycopg[binary] only in the local test environment. No app code changes.
Credentials are read from the ignored backend/.env and never printed.
"""
import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import quote

import psycopg
from psycopg.types.json import Jsonb

from supabase_probe import ENV, ROOT
from verify_supabase_storage import request


def main():
    proof_dir = ROOT / 'backend/storage/supabase-proof'
    storage_proof = json.loads((proof_dir / 'latest.json').read_text())
    run_id = storage_proof['run_id']
    manifests = sorted((ROOT / 'backend/storage').glob('*/manifest.json'))
    report = {'run_id': run_id, 'connection': 'session pooler, TLS',
              'tables': ['demo_verification_documents', 'demo_verification_charts'], 'documents': []}
    with psycopg.connect(host=ENV['SUPABASE_POOLER_HOST'], port=5432, dbname='postgres',
                         user=ENV['SUPABASE_POOLER_USER'], password=ENV['SUPABASE_DB_PASSWORD'],
                         sslmode='require', connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT current_database(), current_user')
            report['database'], report['database_user'] = cur.fetchone()
            cur.execute('''
                CREATE TABLE IF NOT EXISTS public.demo_verification_documents (
                    run_id text NOT NULL, document_id text NOT NULL,
                    name text NOT NULL, page_count integer NOT NULL, chart_count integer NOT NULL,
                    source_pdf_count integer NOT NULL, manifest_sha256 text NOT NULL,
                    manifest_object_key text NOT NULL, payload jsonb NOT NULL,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY (run_id, document_id)
                );
                CREATE TABLE IF NOT EXISTS public.demo_verification_charts (
                    run_id text NOT NULL, document_id text NOT NULL, chart_id text NOT NULL,
                    page_number integer NOT NULL, chart_type text, title text,
                    series_count integer NOT NULL, point_count integer NOT NULL, payload jsonb NOT NULL,
                    PRIMARY KEY (run_id, document_id, chart_id),
                    FOREIGN KEY (run_id, document_id)
                      REFERENCES public.demo_verification_documents (run_id, document_id)
                );
                ALTER TABLE public.demo_verification_documents ENABLE ROW LEVEL SECURITY;
                ALTER TABLE public.demo_verification_charts ENABLE ROW LEVEL SECURITY;
                REVOKE ALL ON public.demo_verification_documents, public.demo_verification_charts FROM anon, authenticated;
                GRANT SELECT, INSERT, UPDATE, DELETE ON public.demo_verification_documents, public.demo_verification_charts TO service_role;
            ''')
            for path in manifests:
                manifest = json.loads(path.read_bytes())
                doc_id = manifest['document_id']
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                pdf_count = sum(s.get('kind') == 'pdf' for s in manifest.get('sources', []))
                cur.execute('''INSERT INTO public.demo_verification_documents
                    (run_id, document_id, name, page_count, chart_count, source_pdf_count,
                     manifest_sha256, manifest_object_key, payload)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (run_id, document_id) DO UPDATE SET payload=EXCLUDED.payload''',
                    (run_id, doc_id, manifest['name'], manifest.get('page_count', 0), len(manifest.get('charts', [])),
                     pdf_count, digest, f'{run_id}/{path.parent.name}/manifest.json', Jsonb(manifest)))
                for chart in manifest.get('charts', []):
                    spec = chart['versions'][chart['current_version']]['spec']
                    series = spec.get('series', [])
                    cur.execute('''INSERT INTO public.demo_verification_charts
                        (run_id, document_id, chart_id, page_number, chart_type, title, series_count, point_count, payload)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (run_id, document_id, chart_id) DO UPDATE SET payload=EXCLUDED.payload''',
                        (run_id, doc_id, chart['chart_id'], chart['page_number'], spec.get('chart_type'), spec.get('title'),
                         len(series), sum(len(s.get('points', [])) for s in series), Jsonb(chart)))
                cur.execute('SELECT payload FROM public.demo_verification_documents WHERE run_id=%s AND document_id=%s', (run_id, doc_id))
                assert cur.fetchone()[0] == manifest, 'Database JSONB roundtrip failed'
                report['documents'].append({'document_id': doc_id, 'pdf_sources': pdf_count,
                    'local_original_pdf_present': (path.parent / 'original.pdf').exists(),
                    'pages': manifest.get('page_count', 0), 'charts': len(manifest.get('charts', [])),
                    'manifest_sha256': digest, 'jsonb_roundtrip_verified': True})
                print(f'PASS PostgreSQL manifest {doc_id}: full JSONB matches', flush=True)
            cur.execute('''SELECT count(*), coalesce(sum(source_pdf_count),0), coalesce(sum(page_count),0), coalesce(sum(chart_count),0)
                FROM public.demo_verification_documents WHERE run_id=%s''', (run_id,))
            counts = cur.fetchone()
            report.update(dict(zip(('document_rows', 'pdf_source_reference_count', 'prepared_pages', 'chart_count'), counts)))
            cur.execute('SELECT count(*), coalesce(sum(series_count),0), coalesce(sum(point_count),0) FROM public.demo_verification_charts WHERE run_id=%s', (run_id,))
            report.update(dict(zip(('chart_rows', 'series_count', 'point_count'), cur.fetchone())))
            cur.execute("NOTIFY pgrst, 'reload schema'")
        conn.commit()

    # New HTTP requests verify database persistence outside the SQL connection.
    path = '/rest/v1/demo_verification_documents?run_id=eq.' + quote(run_id) + '&select=*'
    _, body = request('GET', path)
    rows = json.loads(body)
    assert len(rows) == len(manifests)
    for row in rows:
        local = next(json.loads(p.read_bytes()) for p in manifests if json.loads(p.read_bytes())['document_id'] == row['document_id'])
        assert row['payload'] == local
    report['rest_manifest_roundtrip_verified'] = True
    _, chart_body = request('GET', '/rest/v1/demo_verification_charts?run_id=eq.' + quote(run_id) + '&select=*')
    charts = json.loads(chart_body)
    assert len(charts) == report['chart_rows']
    local_charts = {(m['document_id'], c['chart_id']): c for p in manifests for m in [json.loads(p.read_bytes())] for c in m.get('charts', [])}
    assert all(c['payload'] == local_charts[(c['document_id'], c['chart_id'])] for c in charts)
    report['rest_chart_roundtrip_verified'] = True
    report['physical_pdf_files_uploaded'] = sum(f['object_key'].endswith('.pdf') for f in storage_proof['files'])
    report['provenance_note'] = 'Two physical PDFs; four saved manifests. Chart records come from existing repro_before/repro_after fixtures, not new model calls.'
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    (proof_dir / 'database-proof.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
