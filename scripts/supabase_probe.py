"""Standalone Supabase connectivity probe; never prints credentials."""
import json
import socket
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {}
for line in (ROOT / 'backend/.env').read_text().splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        key, value = line.split('=', 1)
        ENV[key.strip()] = value.strip()

if __name__ == '__main__':
    for label, path in [('storage', '/storage/v1/bucket'), ('database_api', '/rest/v1/')]:
        try:
            req = urllib.request.Request(ENV['SUPABASE_URL'] + path, headers={'apikey': ENV['SUPABASE_SECRET_KEY']})
            with urllib.request.urlopen(req, timeout=30) as response:
                body = response.read()
                print(json.dumps({'service': label, 'status': response.status, 'response_bytes': len(body)}))
        except Exception as exc:
            print(json.dumps({'service': label, 'error': str(exc)}))
    try:
        with socket.create_connection((ENV['SUPABASE_DB_HOST'], int(ENV['SUPABASE_DB_PORT'])), timeout=10):
            print('postgres_tcp: reachable')
    except Exception as exc:
        print('postgres_tcp: ' + str(exc))
