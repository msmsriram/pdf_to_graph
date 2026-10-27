"""Local storage or Supabase with a disposable local asset cache.

Layout (under STORAGE_DIR):
  storage/
    index.json                         # history list (all documents, newest first)
    <document_id>/
      original.pdf
      manifest.json                    # full state: pages, charts, versions, progress
      pages/page_001.png ...
      charts/p001_c01.png ...
      page_001.txt ...                 # human-readable XML artifact (debug)

Local mode reads manifest.json. Supabase mode reads the database and restores
originals/images through /storage/<document_id>/<relative path> on demand.
"""
from __future__ import annotations

import json
import hashlib
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import get_settings
from . import supabase_storage as remote

_lock = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root() -> Path:
    return get_settings().storage_path


def doc_dir(document_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", document_id):
        raise ValueError("Invalid document id")
    d = _root() / document_id
    if not d.resolve().is_relative_to(_root().resolve()):
        raise ValueError("Document path escapes storage")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_path(document_id: str) -> Path:
    return doc_dir(document_id) / "manifest.json"


# --------------------------------------------------------------------------- #
# Manifest read/write
# --------------------------------------------------------------------------- #
def read_manifest(document_id: str) -> Optional[dict[str, Any]]:
    p = _manifest_path(document_id)
    if get_settings().storage_backend == "supabase":
        row = remote.get_record(document_id)
        if row is None:
            return None  # Never fall back to stale local records after cloud deletion.
        m = row["manifest"]
        m["_storage_revision"] = row["revision"]
        m["_storage_assets"] = row["assets"]
        return m
    if not p.exists():
        return None
    with _lock:
        return json.loads(p.read_text(encoding="utf-8"))


def write_manifest(manifest: dict[str, Any]) -> None:
    document_id = manifest["document_id"]
    p = _manifest_path(document_id)
    with _lock:
        if get_settings().storage_backend == "supabase":
            remote.save_record(manifest, document_summary(manifest), p.parent)
        temp = p.with_suffix(".json.tmp")
        temp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(p)
        if get_settings().storage_backend == "local":
            _upsert_index(manifest)


def asset_path(document_id: str, relative: str) -> Path:
    """Restore an original/page/crop on demand; verify cached and downloaded bytes."""
    directory = doc_dir(document_id).resolve()
    target = (directory / relative).resolve()
    if not target.is_relative_to(directory) or target == directory:
        raise ValueError("Invalid asset path")
    if get_settings().storage_backend == "supabase":
        m = read_manifest(document_id)
        asset = (m or {}).get("_storage_assets", {}).get(relative)
        if asset:
            with _lock:
                if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != asset["sha256"]:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temp = target.with_suffix(target.suffix + ".tmp")
                    temp.write_bytes(remote.download_asset(asset))
                    temp.replace(target)
        elif not m or not target.is_file():
            raise FileNotFoundError(relative)
    if not target.is_file():
        raise FileNotFoundError(relative)
    return target


def source_paths(document_id: str, manifest: dict) -> list[tuple[str, Path]]:
    """Resolve every uploaded original, never reconstruct a PDF from its renders."""
    sources = manifest.get("sources") or []
    names = (["original.pdf"] if any(s.get("kind") == "pdf" for s in sources) else [])
    names += ["sources/" + s["filename"] for s in sources if s.get("kind") == "image"]
    if not sources and ((doc_dir(document_id) / "original.pdf").is_file() or "original.pdf" in manifest.get("_storage_assets", {})):
        names = ["original.pdf"]
    return [(name, asset_path(document_id, name)) for name in names]


def mark_interrupted() -> None:
    """One backend process per demo service; tasks cannot survive its restart."""
    for entry in read_index():
        if entry.get("status") in {"uploaded", "rendering", "analyzing"}:
            set_status(entry["document_id"], "error", "Processing was interrupted by a server restart. Reprocess the saved originals to try again.",
                       "Server restarted before processing finished")


def create_manifest(
    document_id: str, name: str, original_filename: str, sources: Optional[list[dict[str, Any]]] = None
) -> dict[str, Any]:
    """`sources`: what was uploaded — [{kind: "pdf", filename}, {kind: "image", filename, label}, …].
    A document is one PDF (optional) plus any number of named images; images become
    pages after the PDF's pages."""
    manifest: dict[str, Any] = {
        "document_id": document_id,
        "name": name,
        "original_filename": original_filename,
        "sources": sources or [],
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
            "pages_skipped": 0,
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
    if get_settings().storage_backend == "supabase":
        return remote.list_summaries()
    p = _index_path()
    if not p.exists():
        return []
    with _lock:
        return json.loads(p.read_text(encoding="utf-8"))


def document_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    entry = {
        "document_id": manifest["document_id"],
        "name": manifest["name"],
        "original_filename": manifest["original_filename"],
        "status": manifest["status"],
        "created_at": manifest["created_at"],
        "updated_at": manifest["updated_at"],
        "page_count": manifest.get("page_count", 0),
        "charts_count": manifest.get("charts_count", 0),
        "pages_skipped": (manifest.get("progress") or {}).get("pages_skipped", 0),
        "sources": [
            {"kind": s.get("kind"), "label": s.get("label") or s.get("filename")} for s in (manifest.get("sources") or [])
        ],
    }
    charts = manifest.get("charts") or []
    types: dict[str, int] = {}
    for chart in charts:
        spec = chart["versions"][chart["current_version"]]["spec"]
        kind = spec.get("chart_type", "unknown")
        types[kind] = types.get(kind, 0) + 1
    u = manifest.get("usage") or {}
    blocks = [b for b in (u, u.get("gate")) if b and b.get("totals")]
    entry.update({
        "pdf_count": sum(s.get("kind") == "pdf" for s in manifest.get("sources", [])),
        "chart_types": types,
        "finalized_count": sum(c.get("final_version") == c.get("current_version") and c.get("final_version") is not None for c in charts),
        "estimated_cost_usd": sum(b["totals"].get("cost_usd", 0) or 0 for b in blocks) if any((b.get("pricing") or {}).get("known") for b in blocks) else None,
        "pricing_complete": bool(blocks) and all((b.get("pricing") or {}).get("known", False) and not b["totals"].get("unpriced_calls", 0) and not b["totals"].get("uncertain_calls", 0) for b in blocks),
        "reprocessed_from": manifest.get("reprocessed_from"),
        "source_files_available": manifest.get("source_files_available", True),
    })
    return entry


def _upsert_index(manifest: dict[str, Any]) -> None:
    entry = document_summary(manifest)
    with _lock:
        idx = read_index()
        idx = [e for e in idx if e["document_id"] != manifest["document_id"]]
        idx.insert(0, entry)
        _index_path().write_text(json.dumps(idx, indent=2, ensure_ascii=False), encoding="utf-8")


def delete_document(document_id: str) -> bool:
    import shutil

    d = doc_dir(document_id)
    with _lock:
        if get_settings().storage_backend == "supabase":
            remote.delete_record(document_id)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        if get_settings().storage_backend == "supabase":
            return True
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
