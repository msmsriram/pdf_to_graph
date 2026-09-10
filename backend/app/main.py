"""FastAPI application.

REST  -> upload, history, document detail, version editing, revert, finalize, delete.
WS    -> /ws/documents/{id} live processing feed (status, progress, per-chart events).
Static-> /storage serves page images and chart crops.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "app"

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import costs, pipeline, storage, style_metrics
from .config import get_settings
from .events import bus
from .openai_client import extract_single_chart
from .schemas import RerunRequest, SaveVersionRequest

REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh", "max"}

settings = get_settings()
app = FastAPI(title="PDF Chart Studio API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve rendered pages / chart crops.
app.mount("/storage", StaticFiles(directory=str(settings.storage_path)), name="storage")


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "model": settings.openai_model,
        "gate_model": settings.gate_model if settings.gate_enabled else None,
        "has_key": bool(settings.openai_api_key),
    }


# --------------------------------------------------------------------------- #
# Upload + processing
# --------------------------------------------------------------------------- #
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


@app.post("/api/documents")
async def upload_document(
    files: list[UploadFile] = File(...),
    name: str = Form(""),
    image_labels: str = Form("[]"),
) -> dict:
    """One upload = one document: at most one PDF plus any number of PNG/JPEG images.
    `image_labels` is a JSON list of user-given names, in the order the images appear."""
    try:
        labels = json.loads(image_labels or "[]")
        labels = [str(x) for x in labels] if isinstance(labels, list) else []
    except ValueError:
        labels = []

    pdfs = [f for f in files if (f.filename or "").lower().endswith(".pdf")]
    images = [f for f in files if (f.filename or "").lower().endswith(IMAGE_EXTS)]
    if not pdfs and not images:
        raise HTTPException(status_code=400, detail="Upload a PDF and/or PNG/JPEG images.")
    if len(pdfs) > 1:
        raise HTTPException(status_code=400, detail="One PDF per upload (images can be added alongside it).")
    if len(pdfs) + len(images) != len(files):
        raise HTTPException(status_code=400, detail="Unsupported file type: only .pdf, .png, .jpg/.jpeg and .webp are accepted.")

    document_id = uuid.uuid4().hex[:12]
    d = storage.doc_dir(document_id)
    sources: list[dict] = []

    if pdfs:
        (d / "original.pdf").write_bytes(await pdfs[0].read())
        sources.append({"kind": "pdf", "filename": pdfs[0].filename})

    (d / "sources").mkdir(parents=True, exist_ok=True)
    for i, img in enumerate(images):
        ext = Path(img.filename or "image.png").suffix.lower() or ".png"
        fn = f"img_{i + 1:03d}{ext}"
        (d / "sources" / fn).write_bytes(await img.read())
        label = labels[i].strip() if i < len(labels) and labels[i].strip() else Path(img.filename or fn).stem
        sources.append({"kind": "image", "filename": fn, "label": label, "original_filename": img.filename})

    doc_name = name.strip() or (Path(pdfs[0].filename).stem if pdfs else sources[0]["label"])
    original_filename = pdfs[0].filename if pdfs else (images[0].filename or "images")
    storage.create_manifest(document_id, name=doc_name, original_filename=original_filename, sources=sources)

    # Kick off background processing (fire and forget; progress via WebSocket).
    asyncio.create_task(pipeline.process_document(document_id))
    return {"document_id": document_id, "name": doc_name, "status": "uploaded", "sources": sources}


@app.post("/api/documents/{document_id}/pages/{page_number}/analyze")
async def analyze_page_override(document_id: str, page_number: int) -> dict:
    """'Process anyway': run the extraction model on a page the gate skipped. Returns the document."""
    m = storage.read_manifest(document_id)
    if not m:
        raise HTTPException(status_code=404, detail="Document not found")
    page = next((p for p in m.get("pages", []) if p.get("page_number") == page_number), None)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    if page.get("status") == "analyzed" and page.get("chart_ids"):
        raise HTTPException(status_code=400, detail="This page has already been analyzed.")
    if m.get("status") not in {"complete", "error"}:
        raise HTTPException(status_code=409, detail="Document is still processing.")
    try:
        return await pipeline.analyze_page_now(document_id, page_number)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Analysis failed: {exc}")


# --------------------------------------------------------------------------- #
# History + detail
# --------------------------------------------------------------------------- #
@app.get("/api/documents")
async def list_documents() -> list[dict]:
    return storage.read_index()


@app.get("/api/documents/{document_id}")
async def get_document(document_id: str) -> dict:
    m = storage.read_manifest(document_id)
    if not m:
        raise HTTPException(status_code=404, detail="Document not found")
    # Charts extracted before stroke/text measurement existed get measured on first open.
    if await asyncio.to_thread(_ensure_style_metrics, m, document_id):
        storage.write_manifest(m)
    return m


def _ensure_style_metrics(m: dict, document_id: str) -> bool:
    """Stamp `style_metrics` (derived from the crop image) on every version lacking it,
    or carrying a measurement from an older method (version mismatch)."""
    changed = False

    def stale(spec: dict) -> bool:
        sm = spec.get("style_metrics")
        return not sm or sm.get("v") != style_metrics.METRICS_VERSION

    for chart in m.get("charts", []):
        versions = chart.get("versions", [])
        if not any(stale(v.get("spec", {})) for v in versions):
            continue
        crop = storage.doc_dir(document_id) / chart["crop_image"]
        if not crop.exists():
            continue
        cur = versions[chart.get("current_version", 0)]["spec"] if versions else {}
        metrics = style_metrics.measure(crop, cur.get("plot_rect"), cur)
        if not metrics:
            continue
        for v in versions:
            if stale(v.get("spec", {})):
                v["spec"]["style_metrics"] = metrics
                changed = True
    return changed


@app.delete("/api/documents/{document_id}")
async def delete_document(document_id: str) -> dict:
    storage.delete_document(document_id)
    bus.clear(document_id)
    return {"deleted": True}


# --------------------------------------------------------------------------- #
# Chart version editing
# --------------------------------------------------------------------------- #
@app.post("/api/documents/{document_id}/charts/{chart_id}/versions")
async def save_version(document_id: str, chart_id: str, body: SaveVersionRequest) -> dict:
    try:
        chart = storage.add_version(
            document_id, chart_id, body.spec.model_dump(), label=body.label or "", kind="edit"
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Document or chart not found")
    return chart


@app.post("/api/documents/{document_id}/charts/{chart_id}/revert")
async def revert_version(document_id: str, chart_id: str) -> dict:
    """Step back one version (undo). Never deletes; just moves the pointer."""
    m = storage.read_manifest(document_id)
    if not m:
        raise HTTPException(status_code=404, detail="Document not found")
    chart = storage.find_chart(m, chart_id)
    if not chart:
        raise HTTPException(status_code=404, detail="Chart not found")
    target = max(0, chart["current_version"] - 1)
    return storage.set_current_version(document_id, chart_id, target)


@app.post("/api/documents/{document_id}/charts/{chart_id}/set-version/{version}")
async def set_version(document_id: str, chart_id: str, version: int) -> dict:
    try:
        return storage.set_current_version(document_id, chart_id, version)
    except KeyError:
        raise HTTPException(status_code=404, detail="Document or chart not found")
    except IndexError:
        raise HTTPException(status_code=400, detail="Invalid version index")


@app.post("/api/documents/{document_id}/charts/{chart_id}/finalize")
async def finalize(document_id: str, chart_id: str) -> dict:
    try:
        return storage.finalize_chart(document_id, chart_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Document or chart not found")


@app.post("/api/documents/{document_id}/charts/{chart_id}/rerun")
async def rerun_chart(document_id: str, chart_id: str, body: RerunRequest) -> dict:
    """Re-extract ONE chart from its crop (optionally at a higher reasoning effort /
    different model) and append the result as a new version. v0 is never touched."""
    m = storage.read_manifest(document_id)
    if not m:
        raise HTTPException(status_code=404, detail="Document not found")
    chart = storage.find_chart(m, chart_id)
    if not chart:
        raise HTTPException(status_code=404, detail="Chart not found")
    if body.effort and body.effort not in REASONING_EFFORTS:
        raise HTTPException(status_code=400, detail=f"effort must be one of {sorted(REASONING_EFFORTS)}")

    crop = storage.doc_dir(document_id) / chart["crop_image"]
    if not crop.exists():
        raise HTTPException(status_code=404, detail="Chart crop image is missing")

    prior = chart["versions"][chart["current_version"]]["spec"]
    t0 = time.perf_counter()
    try:
        ec, usage = await asyncio.to_thread(extract_single_chart, crop, prior, body.effort, body.model)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Re-extraction failed: {exc}")
    usage["seconds"] = round(time.perf_counter() - t0, 2)
    usage["cost_usd"] = costs.cost_usd(usage, costs.pricing_for(usage["model"]))

    spec = pipeline._spec_from_extracted(ec)
    spec["style_metrics"] = await asyncio.to_thread(style_metrics.measure, crop, spec.get("plot_rect"), spec)
    chart_out = storage.add_version(
        document_id,
        chart_id,
        spec,
        label=f"Re-extracted · {usage['model']} · {usage['effort']}",
        kind="rerun",
        extra={"usage": usage},
    )

    # Document-level accounting: list the re-run and add it to the totals.
    m2 = storage.read_manifest(document_id)
    ublock = costs.ensure_usage_block(m2, settings.openai_model)
    ublock["reruns"].append({"chart_id": chart_id, "version": chart_out["current_version"], "at": storage._now(), **usage})
    costs.add_to_totals(ublock["totals"], usage)
    storage.write_manifest(m2)
    return chart_out


# --------------------------------------------------------------------------- #
# Live processing feed
# --------------------------------------------------------------------------- #
@app.websocket("/ws/documents/{document_id}")
async def ws_documents(websocket: WebSocket, document_id: str) -> None:
    await websocket.accept()
    # Atomic (no await between): snapshot backlog, then subscribe to future events.
    backlog = bus.get_backlog(document_id)
    queue = bus.subscribe(document_id)
    try:
        for event in backlog:
            await websocket.send_json(event)
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        bus.unsubscribe(document_id, queue)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
