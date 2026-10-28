"""Bounded page batches: concurrent model calls, serialized durable commits.

Every page uses the same bounded document context. Charts are published as soon
as that page commits, and both persisted and live results sort by source order.
"""
from __future__ import annotations

import asyncio
import json
import time
import weakref
from pathlib import Path
from typing import Any

from PIL import Image

from . import costs, gate, pdf_processor, storage, style_metrics
from .config import get_settings
from .events import bus
from .openai_client import extract_page
from .schemas import ExtractedChart

_tasks: dict[str, asyncio.Task] = {}
_runtimes = weakref.WeakKeyDictionary()
_inflight: set[asyncio.Task] = set()


def _runtime():
    loop = asyncio.get_running_loop()
    if loop not in _runtimes:
        s = get_settings()
        _runtimes[loop] = {"all": asyncio.Semaphore(s.llm_concurrency),
                           "gate": asyncio.Semaphore(s.gate_concurrency),
                           "extract": asyncio.Semaphore(s.extraction_concurrency),
                           "commits": {}, "pages": {}}
    return _runtimes[loop]


def _commit_lock(document_id):
    return _runtime()["commits"].setdefault(document_id, asyncio.Lock())


async def _settled(func, *args):
    """Let finite disk/database work settle before cancellation releases its lock."""
    task = asyncio.create_task(asyncio.to_thread(func, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def _write(m):
    await _settled(storage.write_manifest, m)


async def _read(document_id):
    return await asyncio.to_thread(storage.read_manifest, document_id)


async def _model_call(scope, func, *args):
    """Global per-process limits cover every document and both model stages.

The SDK retries transient rate limits with backoff. A cancelled synchronous call
still occupies its slots until the underlying request actually finishes.
"""
    runtime = _runtime()
    stage, total = runtime[scope], runtime["all"]
    queued = time.perf_counter()
    await stage.acquire()
    try:
        await total.acquire()
    except BaseException:
        stage.release()
        raise
    queue_seconds = round(time.perf_counter() - queued, 2)

    def invoke():
        started = time.perf_counter()
        result = func(*args)
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
            result[1]["seconds"] = round(time.perf_counter() - started, 2)
            result[1]["queue_seconds"] = queue_seconds
        return result

    task = asyncio.create_task(asyncio.to_thread(invoke))
    _inflight.add(task)

    def finished(task):
        _inflight.discard(task)
        if not task.cancelled():
            task.exception()  # Consume failures from calls abandoned by cancellation.

    task.add_done_callback(finished)
    deferred = False
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        deferred = True
        task.add_done_callback(lambda _: (stage.release(), total.release()))
        raise
    finally:
        if not deferred:
            stage.release()
            total.release()


async def _emit(document_id: str, event: dict[str, Any]) -> None:
    await bus.publish(document_id, {**event, "document_id": document_id})


async def _log(document_id: str, message: str, level: str = "info") -> None:
    await _emit(document_id, {"type": "log", "level": level, "message": message})


def _page_xml(page_number: int, summary: str, charts: list[dict]) -> str:
    return json.dumps({"page_number": page_number, "summary": summary, "charts": charts}, ensure_ascii=False, indent=2)


def _image_to_page_png(src: Path, dst: Path) -> dict[str, Any]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as original:
        image = original.convert("RGB")
        image.save(dst)
        return {"width": image.width, "height": image.height}


def _find_page(m: dict[str, Any], page_no: int) -> dict[str, Any]:
    return next(p for p in m["pages"] if p["page_number"] == page_no)


def _label(p: dict[str, Any]) -> str:
    return p.get("label") or f"Page {p['page_number']}"


def start(document_id: str) -> None:
    if document_id in _tasks and not _tasks[document_id].done():
        return
    task = asyncio.create_task(process_document(document_id))
    _tasks[document_id] = task
    task.add_done_callback(lambda t: _tasks.pop(document_id, None) if _tasks.get(document_id) is t else None)


async def cancel(document_id: str) -> bool:
    task = _tasks.get(document_id)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return True
    m = await _read(document_id)
    if m and m.get("status") in {"uploaded", "rendering", "analyzing"}:
        await _mark_cancelled(document_id)
        return True
    return False


async def _mark_cancelled(document_id: str) -> None:
    async with _commit_lock(document_id):
        m = await _read(document_id)
        if not m:
            return
        message = f"Stopped by user — {len(m.get('charts', []))} chart(s) saved before stopping"
        m.update(status="cancelled", status_message=message, updated_at=storage._now())
        await _write(m)
        await _emit(document_id, {"type": "status", "status": "cancelled", "message": message})


async def _screen_page(document_id, pm):
    s = get_settings()
    page_no, label = pm["page_number"], pm["label"]
    path = await asyncio.to_thread(storage.asset_path, document_id, f"pages/{pm['filename']}")
    await _log(document_id, f"Screening {label} with {s.gate_model}")
    started = time.perf_counter()
    verdict, usage = await _model_call("gate", gate.classify_page, path)
    usage.setdefault("seconds", round(time.perf_counter() - started, 2))
    probability = gate.chart_probability(verdict)
    skipped = probability < s.gate_threshold
    async with _commit_lock(document_id):
        m = await _read(document_id)
        page = _find_page(m, page_no)
        block = costs.ensure_gate_block(m, s.gate_model)
        usage["cost_usd"] = costs.cost_usd(usage, block["pricing"])
        costs.add_to_totals(block["totals"], usage)
        page["gate"] = {**verdict.model_dump(), "p_charts": round(probability, 3), "skipped": skipped, "usage": usage}
        if skipped:
            page.update(status="skipped", summary=f"Skipped by gate: {verdict.reason}")
            m["progress"]["pages_analyzed"] += 1
            m["progress"]["pages_skipped"] = m["progress"].get("pages_skipped", 0) + 1
        m["updated_at"] = storage._now()
        await _write(m)
        await _emit(document_id, {"type": "usage", "scope": "gate", "page_number": page_no, "usage": usage,
                                  "totals": block["totals"], "model": block["model"], "pricing": block["pricing"], "verdict": page["gate"]})
        await _emit(document_id, {"type": "progress", "progress": m["progress"]})
    if skipped:
        await _log(document_id, f"{label}: no charts (gate {probability:.0%}) — skipped. {verdict.reason}")
        await _emit(document_id, {"type": "page_skipped", "page_number": page_no, "reason": verdict.reason})
    else:
        await _log(document_id, f"{label}: gate sees {verdict.chart_count} chart(s) ({probability:.0%}) → extracting with {s.openai_model}")
    return skipped


async def _run_page(document_id, pm, context):
    s = get_settings()
    try:
        use_gate = s.gate_enabled and s.gate_model and (pm["kind"] == "pdf" or s.gate_images)
        if use_gate and await _screen_page(document_id, pm):
            return
        await analyze_page(document_id, pm["page_number"], context)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        async with _commit_lock(document_id):
            m = await _read(document_id)
            page = _find_page(m, pm["page_number"])
            if page["status"] not in {"analyzed", "skipped", "error"}:
                m["progress"]["pages_analyzed"] += 1
            page.update(status="error", error=str(exc))
            await _write(m)
            await _emit(document_id, {"type": "progress", "progress": m["progress"]})
        await _log(document_id, f"{pm['label']}: processing failed — {type(exc).__name__}: {exc}", "error")


async def _run_batch(document_id, pages, context):
    tasks = [asyncio.create_task(_run_page(document_id, p, context)) for p in pages]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def process_document(document_id: str) -> None:
    s = get_settings()
    d = storage.doc_dir(document_id)
    try:
        await _settled(storage.set_status, document_id, "rendering", "Preparing pages")
        await _emit(document_id, {"type": "status", "status": "rendering", "message": "Preparing pages"})
        m = await _read(document_id)
        originals = await asyncio.to_thread(storage.source_paths, document_id, m)
        context = ""
        pages = []
        if any(relative == "original.pdf" for relative, _ in originals):
            pdf = d / "original.pdf"
            m["original_pdf_page_count"] = await asyncio.to_thread(pdf_processor.page_count, pdf)
            context = await asyncio.to_thread(pdf_processor.document_context, pdf, s.max_pages)
            if m["original_pdf_page_count"] > s.max_pages:
                await _log(document_id, f"Processing first {s.max_pages} of {m['original_pdf_page_count']} PDF pages (MAX_PAGES cap).", "warn")
            rendered = await _settled(pdf_processor.render_pdf_to_pngs, pdf, d / "pages", s.render_dpi, s.max_pages)
            pages.extend({**p, "kind": "pdf", "label": f"Page {p['page_number']}"} for p in rendered)
        for source in m.get("sources", []):
            if source.get("kind") != "image":
                continue
            number = len(pages) + 1
            filename = f"page_{number:03d}.png"
            meta = await _settled(_image_to_page_png, d / "sources" / source["filename"], d / "pages" / filename)
            pages.append({"page_number": number, "filename": filename, **meta, "kind": "image", "label": source.get("label") or source["filename"]})
        m["page_count"] = len(pages)
        m["pages"] = [{"page_number": p["page_number"], "kind": p["kind"], "label": p["label"],
                       "image": f"pages/{p['filename']}", "width": p["width"], "height": p["height"],
                       "summary": "", "status": "pending", "gate": None, "has_charts": False, "chart_ids": []} for p in pages]
        m["progress"].update(pages_total=len(pages), pages_rendered=len(pages))
        m["document_context"] = context
        m["processing_config"] = {"page_batch_size": s.page_batch_size, "llm_concurrency": s.llm_concurrency,
                                   "gate_concurrency": s.gate_concurrency, "extraction_concurrency": s.extraction_concurrency,
                                   "context_mode": "shared_native_text", "started_at": storage._now()}
        await _write(m)
        for p in m["pages"]:
            await _emit(document_id, {"type": "page_rendered", **p})
        await _emit(document_id, {"type": "progress", "progress": m["progress"]})

        for offset in range(0, len(pages), s.page_batch_size):
            group = pages[offset:offset + s.page_batch_size]
            message = f"Processing pages {offset + 1}–{offset + len(group)} of {len(pages)} · up to {s.llm_concurrency} model calls at once"
            await _settled(storage.set_status, document_id, "analyzing", message)
            await _emit(document_id, {"type": "status", "status": "analyzing", "message": message})
            await _run_batch(document_id, group, context)

        m = await _read(document_id)
        failed = [p for p in m["pages"] if p["status"] == "error"]
        message = f"{len(m['charts'])} charts saved · {m['progress'].get('pages_skipped', 0)} pages skipped"
        m.update(status="error" if failed else "complete", status_message=message,
                 error=f"{len(failed)} page(s) failed; successful pages were retained" if failed else None,
                 updated_at=storage._now(), processing_finished_at=storage._now())
        await _write(m)
        await _emit(document_id, {"type": "status", "status": m["status"], "message": message})
        await _emit(document_id, {"type": "error", "message": m["error"]} if failed else {"type": "complete", "charts_count": len(m["charts"])})
    except asyncio.CancelledError:
        await _mark_cancelled(document_id)
        raise
    except Exception as exc:
        await _settled(storage.set_status, document_id, "error", "Processing failed", str(exc))
        await _emit(document_id, {"type": "error", "message": str(exc)})


async def analyze_page(document_id: str, page_no: int, running_notes: str = "") -> str:
    lock = _runtime()["pages"].setdefault((document_id, page_no), asyncio.Lock())
    async with lock:
        s = get_settings()
        m = await _read(document_id)
        page = _find_page(m, page_no)
        if page["status"] == "analyzed":
            return page.get("context_notes", "")
        path = await asyncio.to_thread(storage.asset_path, document_id, page["image"])
        await _log(document_id, f"Sending {_label(page)} to {s.openai_model} ({s.reasoning_effort} reasoning)…")
        started = time.perf_counter()
        extraction, usage = await _model_call("extract", extract_page, path, page_no, running_notes)
        usage.setdefault("seconds", round(time.perf_counter() - started, 2))

        async with _commit_lock(document_id):
            m = await _read(document_id)
            page = _find_page(m, page_no)
            page.update(summary=extraction.summary, has_charts=bool(extraction.charts), status="analyzed",
                        context_notes=(extraction.updated_running_notes or "")[:2000])
            page.pop("error", None)
            m["progress"]["pages_analyzed"] += 1
            block = costs.ensure_usage_block(m, s.openai_model)
            usage["cost_usd"] = costs.cost_usd(usage, block["pricing"])
            page["usage"] = usage
            costs.add_to_totals(block["totals"], usage)
            created = []
            existing = {c["chart_id"] for c in m["charts"]}
            index = 0
            for extracted in extraction.charts:
                index += 1
                cid = f"p{page_no:03d}_c{index:02d}"
                while cid in existing:
                    index += 1
                    cid = f"p{page_no:03d}_c{index:02d}"
                existing.add(cid)
                rel = f"charts/{cid}.png"
                crop = storage.doc_dir(document_id) / rel
                crop_meta = await _settled(pdf_processor.crop_chart, path, extracted.bbox, crop)
                spec = _spec_from_extracted(extracted, crop_box=crop_meta.get("box_norm"))
                spec["style_metrics"] = await asyncio.to_thread(style_metrics.measure, crop, spec.get("plot_rect"), spec)
                chart = {"chart_id": cid, "page_number": page_no, "crop_image": rel, "bbox": extracted.bbox,
                         "confidence": extracted.confidence.model_dump(), "status": "extracted", "current_version": 0,
                         "final_version": None, "versions": [{"version": 0, "label": "Original extraction", "kind": "original",
                                                               "created_at": storage._now(), "spec": spec}]}
                m["charts"].append(chart)
                page["chart_ids"].append(cid)
                created.append(chart)
            m["charts"].sort(key=lambda c: (c["page_number"], int(c["chart_id"].rsplit("_c", 1)[1])))
            m["charts_count"] = len(m["charts"])
            m["progress"].update(charts_detected=len(m["charts"]), charts_extracted=len(m["charts"]))
            m["updated_at"] = storage._now()
            (storage.doc_dir(document_id) / f"page_{page_no:03d}.txt").write_text(_page_xml(page_no, extraction.summary, created), encoding="utf-8")
            await _write(m)
            await _emit(document_id, {"type": "usage", "scope": "extract", "page_number": page_no, "usage": usage,
                                      "totals": block["totals"], "model": block["model"], "pricing": block["pricing"]})
            for chart in created:
                await _emit(document_id, {"type": "chart_detected", "chart_id": chart["chart_id"], "page_number": page_no, "crop_image": chart["crop_image"]})
                await _emit(document_id, {"type": "chart_extracted", "chart": chart})
            await _emit(document_id, {"type": "progress", "progress": m["progress"]})
            await _emit(document_id, {"type": "page_analyzed", "page_number": page_no, "summary": extraction.summary, "charts_found": len(created)})
        await _log(document_id, f"{_label(page)}: {len(created)} chart(s) extracted in {usage['seconds']}s")
        return page["context_notes"]


async def analyze_page_now(document_id: str, page_no: int) -> dict[str, Any]:
    async with _commit_lock(document_id):
        m = await _read(document_id)
        page = _find_page(m, page_no)
        if page["status"] == "analyzed":
            return m
        if page["status"] in {"skipped", "error"}:
            m["progress"]["pages_analyzed"] = max(0, m["progress"]["pages_analyzed"] - 1)
        if page["status"] == "skipped":
            m["progress"]["pages_skipped"] = max(0, m["progress"].get("pages_skipped", 0) - 1)
            page["gate"].update(skipped=False, overridden=True)
        page["status"] = "pending"
        await _write(m)
    await analyze_page(document_id, page_no, m.get("document_context", ""))
    m = await _read(document_id)
    failed = sum(p["status"] == "error" for p in m["pages"])
    m.update(status="error" if failed else "complete", error=f"{failed} page(s) failed" if failed else None,
             status_message=f"{len(m['charts'])} charts saved", updated_at=storage._now())
    await _write(m)
    return m


def _plot_rect_in_crop(plot: list[float] | None, crop_box: list[float] | None) -> list[float] | None:
    """Map the model's plot-area rectangle into the crop's own frame (0..1).

    `crop_box` is the crop rectangle in page-normalized coords (page flow). When it is
    None the plot rectangle is already relative to the crop (single-chart re-extraction).
    Returns None for missing/implausible rectangles so the UI simply hides the overlay.
    """
    if not plot or len(plot) != 4:
        return None
    try:
        x0, y0, x1, y1 = [float(v) for v in plot]
        if crop_box:
            cx0, cy0, cx1, cy1 = [float(v) for v in crop_box]
            cw, ch = cx1 - cx0, cy1 - cy0
            if cw <= 0 or ch <= 0:
                return None
            x0, x1 = (x0 - cx0) / cw, (x1 - cx0) / cw
            y0, y1 = (y0 - cy0) / ch, (y1 - cy0) / ch
        clamp = lambda v: max(0.0, min(1.0, v))  # noqa: E731
        x0, y0, x1, y1 = clamp(x0), clamp(y0), clamp(x1), clamp(y1)
        if x1 - x0 < 0.2 or y1 - y0 < 0.2:
            return None
        return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]
    except (TypeError, ValueError):
        return None


def _spec_from_extracted(ec: ExtractedChart, crop_box: list[float] | None = None) -> dict:
    """Convert the model's ExtractedChart into the stored/editable ChartSpec dict."""
    return {
        "title": ec.title,
        "subtitle": ec.subtitle,
        "title_position": ec.title_position,
        "chart_type": ec.chart_type,
        "stacked": ec.stacked,
        "x_axis": ec.x_axis.model_dump(),
        "y_axis": ec.y_axis.model_dump(),
        "series": [s.model_dump() for s in ec.series],
        "legend": ec.legend,
        "inline_labels": ec.inline_labels,
        "show_markers": ec.show_markers,
        "smooth": ec.smooth,
        "annotations": [a.model_dump() for a in ec.annotations],
        "plot_rect": _plot_rect_in_crop(ec.plot_bbox, crop_box),
        "notes": ec.notes,
        "confidence": ec.confidence.model_dump(),
    }
