"""Supabase REST adapter. Original bytes live in private Storage; state in Postgres.

No privileged credentials reach the browser. Local files are a disposable cache.
Content-addressed object keys keep a failed state update from changing old assets.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .config import get_settings


class StorageError(RuntimeError):
    pass


class StorageConflict(StorageError):
    pass


def request(method: str, path: str, body=None, headers=None, *, binary=False):
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise StorageError("Set SUPABASE_URL and SUPABASE_SECRET_KEY for Supabase storage")
    if not settings.supabase_url.startswith("https://"):
        raise StorageError("SUPABASE_URL must use HTTPS")
    h = {"apikey": settings.supabase_secret_key, "Content-Type": "application/json", **(headers or {})}
    data = body if isinstance(body, bytes) or body is None else json.dumps(body, ensure_ascii=False).encode()
    # Reads, conditional patches, content-addressed uploads and deletes are retryable.
    # New row inserts are not blindly retried after an uncertain outcome.
    attempts = 3 if method != "POST" or "x-upsert" in h else 1
    for attempt in range(attempts):
        try:
            req = Request(settings.supabase_url.rstrip("/") + path, data=data, method=method, headers=h)
            with urlopen(req, timeout=60) as response:
                payload = response.read()
                return payload if binary else (json.loads(payload) if payload else None)
        except HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt + 1 < attempts:
                time.sleep(0.3 * (attempt + 1))
                continue
            # Do not print HTTP request headers or privileged server diagnostics.
            raise StorageError(f"Supabase {method} failed (HTTP {exc.code})") from None
        except (URLError, TimeoutError, OSError):
            if attempt + 1 == attempts:
                raise StorageError("Supabase could not be reached; data was not confirmed saved") from None
            time.sleep(0.3 * (attempt + 1))


def _table() -> str:
    return "/rest/v1/" + quote(get_settings().supabase_table, safe="")


def get_record(document_id: str):
    rows = request("GET", _table() + "?" + urlencode({"document_id": "eq." + document_id, "select": "*"}))
    return rows[0] if rows else None


def list_summaries() -> list[dict]:
    result = []
    offset = 0
    while True:
        rows = request("GET", _table() + "?" + urlencode({"select": "summary", "order": "created_at.desc,document_id.asc", "limit": 500, "offset": offset}))
        result.extend(row["summary"] for row in rows)
        if len(rows) < 500:
            return result
        offset += len(rows)


def save_record(manifest: dict, summary: dict, directory) -> None:
    document_id = manifest["document_id"]
    revision = manifest.get("_storage_revision")
    assets = dict(manifest.get("_storage_assets") or {})
    # Never remove remote assets just because a fresh instance has an empty cache.
    for file in sorted(directory.rglob("*")):
        if not file.is_file() or file.name == "manifest.json" or file.name.endswith(".tmp"):
            continue
        rel = file.relative_to(directory).as_posix()
        payload = file.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if assets.get(rel, {}).get("sha256") == digest:
            continue
        key = f"{document_id}/{digest}/{rel}"
        bucket = quote(get_settings().supabase_bucket, safe="")
        request("POST", f"/storage/v1/object/{bucket}/{quote(key, safe='/')}", payload,
                {"Content-Type": mimetypes.guess_type(file.name)[0] or "application/octet-stream", "x-upsert": "true"})
        assets[rel] = {"key": key, "sha256": digest, "bytes": len(payload)}
    persisted = {k: v for k, v in manifest.items() if k not in ("_storage_revision", "_storage_assets")}
    new_revision = (revision or 0) + 1
    row = {"document_id": document_id, "manifest": persisted, "summary": summary,
           "assets": assets, "revision": new_revision, "created_at": manifest["created_at"],
           "updated_at": manifest["updated_at"]}
    if revision is None:
        request("POST", _table(), row, {"Prefer": "return=representation"})
    else:
        query = urlencode({"document_id": "eq." + document_id, "revision": "eq." + str(revision)})
        rows = request("PATCH", _table() + "?" + query, row, {"Prefer": "return=representation"})
        if not rows:
            # A response lost after a successful write is safe to acknowledge only
            # if the complete state and asset map match what we tried to commit.
            current = get_record(document_id)
            if not current or current["revision"] != new_revision or current["manifest"] != persisted or current["assets"] != assets:
                raise StorageConflict("Document changed while saving; reload and try again")
    manifest["_storage_revision"] = new_revision
    manifest["_storage_assets"] = assets


def download_asset(asset: dict) -> bytes:
    bucket = quote(get_settings().supabase_bucket, safe="")
    data = request("GET", f"/storage/v1/object/{bucket}/{quote(asset['key'], safe='/')}", binary=True)
    if hashlib.sha256(data).hexdigest() != asset["sha256"]:
        raise StorageError("Downloaded asset failed its integrity check")
    return data


def delete_record(document_id: str) -> None:
    # Objects are content-addressed: include older keys, not only current assets.
    bucket = quote(get_settings().supabase_bucket, safe="")
    keys = []

    def collect(prefix):
        offset = 0
        while True:
            rows = request("POST", f"/storage/v1/object/list/{bucket}", {"prefix": prefix, "limit": 100, "offset": offset})
            for row in rows:
                key = prefix + "/" + row["name"]
                if row.get("id") is None:
                    collect(key)
                else:
                    keys.append(key)
            if len(rows) < 100:
                return
            offset += len(rows)

    collect(document_id)
    for start in range(0, len(keys), 100):
        request("DELETE", f"/storage/v1/object/{bucket}", {"prefixes": keys[start:start + 100]})
    request("DELETE", _table() + "?" + urlencode({"document_id": "eq." + document_id}))
