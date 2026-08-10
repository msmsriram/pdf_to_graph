"""FastAPI application.

REST  -> upload, history, document detail, version editing, revert, finalize, delete.
WS    -> /ws/documents/{id} live processing feed (status, progress, per-chart events).
Static-> /storage serves page images and chart crops.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "app"

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import pipeline, storage
from .config import get_settings
from .events import bus
from .schemas import SaveVersionRequest

settings = get_settings()
app = FastAPI(title="PDF Chart Studio API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve rendered pages / chart crops.
app.mount("/storage", StaticFiles(directory=str(settings.storage_path)), name="storage")


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "model": settings.openai_model, "has_key": bool(settings.openai_api_key)}


# --------------------------------------------------------------------------- #
# Upload + processing
# --------------------------------------------------------------------------- #
@app.post("/api/documents")
async def upload_document(file: UploadFile = File(...)) -> dict:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf files are accepted.")

    document_id = uuid.uuid4().hex[:12]
    d = storage.doc_dir(document_id)
    data = await file.read()
    (d / "original.pdf").write_bytes(data)

    name = file.filename.rsplit(".", 1)[0]
    storage.create_manifest(document_id, name=name, original_filename=file.filename)

    # Kick off background processing (fire and forget; progress via WebSocket).
    import asyncio

    asyncio.create_task(pipeline.process_document(document_id))

    return {"document_id": document_id, "name": name, "status": "uploaded"}


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
    return m


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
