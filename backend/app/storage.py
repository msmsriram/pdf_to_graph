"""Filesystem storage.

Layout (under STORAGE_DIR):
  storage/
    index.json                         # history list (all documents, newest first)
    <document_id>/
      original.pdf
      manifest.json                    # full state: pages, charts, versions, progress
      pages/page_001.png ...
      charts/p001_c01.png ...
      page_001.txt ...                 # human-readable XML artifact (debug)

manifest.json is the single source of truth the API reads/writes. Image files
are served statically at /storage/<document_id>/<relative path>.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import get_settings

_lock = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root() -> Path:
    return get_settings().storage_path


def doc_dir(document_id: str) -> Path:
    d = _root() / document_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_path(document_id: str) -> Path:
    return doc_dir(document_id) / "manifest.json"


# --------------------------------------------------------------------------- #
# Manifest read/write
# --------------------------------------------------------------------------- #
def read_manifest(document_id: str) -> Optional[dict[str, Any]]:
    p = _manifest_path(document_id)
    if not p.exists():
        return None
    with _lock:
        return json.loads(p.read_text(encoding="utf-8"))


def write_manifest(manifest: dict[str, Any]) -> None:
    document_id = manifest["document_id"]
    p = _manifest_path(document_id)
    with _lock:
        p.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    _upsert_index(manifest)


def create_manifest(document_id: str, name: str, original_filename: str) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "document_id": document_id,
        "name": name,
        "original_filename": original_filename,
        "status": "uploaded",
        "status_message": "Uploaded, waiting to process",
        "created_at": _now(),
        "updated_at": _now(),
        "page_count": 0,
        "charts_count": 0,
        "pages": [],
        "charts": [],
        "progress": {
            "pages_total": 0,
            "pages_rendered": 0,
            "pages_analyzed": 0,
            "charts_detected": 0,
            "charts_extracted": 0,
        },
        "error": None,
    }
    write_manifest(manifest)
    return manifest


def set_status(document_id: str, status: str, message: str = "", error: Optional[str] = None) -> dict[str, Any]:
    m = read_manifest(document_id)
    if not m:
        raise KeyError(document_id)
    m["status"] = status
    m["status_message"] = message
    m["updated_at"] = _now()
    if error is not None:
        m["error"] = error
    write_manifest(m)
    return m


# --------------------------------------------------------------------------- #
# History index
# --------------------------------------------------------------------------- #
def _index_path() -> Path:
    return _root() / "index.json"


def read_index() -> list[dict[str, Any]]:
    p = _index_path()
    if not p.exists():
        return []
    with _lock:
        return json.loads(p.read_text(encoding="utf-8"))


def _upsert_index(manifest: dict[str, Any]) -> None:
    entry = {
        "document_id": manifest["document_id"],
        "name": manifest["name"],
        "original_filename": manifest["original_filename"],
        "status": manifest["status"],
        "created_at": manifest["created_at"],
        "updated_at": manifest["updated_at"],
        "page_count": manifest.get("page_count", 0),
        "charts_count": manifest.get("charts_count", 0),
    }
    with _lock:
        idx = read_index()
        idx = [e for e in idx if e["document_id"] != manifest["document_id"]]
        idx.insert(0, entry)
        _index_path().write_text(json.dumps(idx, indent=2, ensure_ascii=False), encoding="utf-8")


def delete_document(document_id: str) -> bool:
    import shutil

    d = _root() / document_id
    with _lock:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        idx = [e for e in read_index() if e["document_id"] != document_id]
        _index_path().write_text(json.dumps(idx, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


# --------------------------------------------------------------------------- #
# Chart / version helpers
# --------------------------------------------------------------------------- #
def find_chart(manifest: dict[str, Any], chart_id: str) -> Optional[dict[str, Any]]:
    for c in manifest.get("charts", []):
        if c["chart_id"] == chart_id:
            return c
    return None


def add_version(
    document_id: str, chart_id: str, spec: dict[str, Any], label: str, kind: str, extra: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    """Append a new version to a chart and make it current. Returns the chart.
    `extra` (e.g. {"usage": ...}) is merged into the version record."""
    m = read_manifest(document_id)
    if not m:
        raise KeyError(document_id)
    chart = find_chart(m, chart_id)
    if not chart:
        raise KeyError(chart_id)
    next_version = len(chart["versions"])
    version = {
        "version": next_version,
        "label": label or f"Version {next_version}",
        "kind": kind,  # "original" | "edit" | "rerun"
        "created_at": _now(),
        "spec": spec,
    }
    if extra:
        version.update(extra)
    chart["versions"].append(version)
    chart["current_version"] = next_version
    m["updated_at"] = _now()
    write_manifest(m)
    return chart


def set_current_version(document_id: str, chart_id: str, version: int) -> dict[str, Any]:
    m = read_manifest(document_id)
    if not m:
        raise KeyError(document_id)
    chart = find_chart(m, chart_id)
    if not chart:
        raise KeyError(chart_id)
    if version < 0 or version >= len(chart["versions"]):
        raise IndexError(version)
    chart["current_version"] = version
    m["updated_at"] = _now()
    write_manifest(m)
    return chart


def finalize_chart(document_id: str, chart_id: str) -> dict[str, Any]:
    m = read_manifest(document_id)
    if not m:
        raise KeyError(document_id)
    chart = find_chart(m, chart_id)
    if not chart:
        raise KeyError(chart_id)
    chart["final_version"] = chart["current_version"]
    chart["status"] = "final"
    m["updated_at"] = _now()
    write_manifest(m)
    return chart
