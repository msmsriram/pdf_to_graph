"""Document processing pipeline.

Runs as a background asyncio task. Heavy/blocking work (PDF render, model calls,
image crop) is pushed to threads so the event loop keeps flushing WebSocket
events. Every meaningful step emits an event on the bus for the live UI.

A document = one optional PDF + any number of user-named images. PDF pages are
rendered first (page 1..N); images follow as pages N+1.. with the user's label.

Per page:  gate (cheap model: "any chart here?")  →  skip, or  →  extraction (Astra).
Uploaded images skip the gate by default (explicit user intent). Skipped pages keep
their gate verdict and can be processed later via `analyze_page_now` ("Process anyway").
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from PIL import Image

from . import costs, gate, pdf_processor, storage, style_metrics
from .config import get_settings
from .events import bus
from .openai_client import extract_page
from .schemas import ExtractedChart


async def _emit(document_id: str, event: dict[str, Any]) -> None:
    event["document_id"] = document_id
    await bus.publish(document_id, event)


async def _log(document_id: str, message: str, level: str = "info") -> None:
    await _emit(document_id, {"type": "log", "level": level, "message": message})


def _page_xml(page_number: int, summary: str, charts: list[dict]) -> str:
    """Human-readable XML artifact written per page (debug / traceability)."""
    lines = [f'<page number="{page_number}">', "  <summary>", f"    {summary}", "  </summary>"]
    if charts:
        lines.append("  <charts>")
        for c in charts:
            spec = c["versions"][0]["spec"]
            lines.append(f'    <chart id="{c["chart_id"]}" type="{spec.get("chart_type")}">')
            lines.append(f"      {json.dumps(spec, ensure_ascii=False)}")
            lines.append("    </chart>")
        lines.append("  </charts>")
    lines.append("</page>")
    return "\n".join(lines)


def _image_to_page_png(src: Path, dst: Path) -> dict[str, Any]:
    """Normalise an uploaded image (png/jpeg/webp) into a page PNG."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(src).convert("RGB")
    img.save(str(dst))
    return {"width": img.width, "height": img.height}


def _find_page(m: dict[str, Any], page_no: int) -> dict[str, Any]:
    return next(p for p in m["pages"] if p["page_number"] == page_no)


def _label(p: dict[str, Any]) -> str:
    return p.get("label") or f"Page {p['page_number']}"


# --------------------------------------------------------------------------- #
# Whole document
# --------------------------------------------------------------------------- #
async def process_document(document_id: str) -> None:
    settings = get_settings()
    d = storage.doc_dir(document_id)

    try:
        # -------- 1. Render PDF pages + normalise images into pages --------
        await _emit(document_id, {"type": "status", "status": "rendering", "message": "Preparing pages"})
        storage.set_status(document_id, "rendering", "Preparing pages")

        m = storage.read_manifest(document_id)
        sources = m.get("sources") or []
        has_pdf = (d / "original.pdf").exists()
        image_srcs = [s for s in sources if s.get("kind") == "image"]

        pages_meta: list[dict[str, Any]] = []
        if has_pdf:
            pdf_path = d / "original.pdf"
            total = pdf_processor.page_count(pdf_path)
            if total > settings.max_pages:
                await _log(document_id, f"PDF has {total} pages; processing first {settings.max_pages} (MAX_PAGES cap).", "warn")
            rendered = await asyncio.to_thread(
                pdf_processor.render_pdf_to_pngs, pdf_path, d / "pages", settings.render_dpi, settings.max_pages
            )
            for pm in rendered:
                pm["kind"] = "pdf"
                pm["label"] = f"Page {pm['page_number']}"
            pages_meta.extend(rendered)

        next_no = len(pages_meta) + 1
        for src in image_srcs:
            fn = f"page_{next_no:03d}.png"
            meta = await asyncio.to_thread(_image_to_page_png, d / "sources" / src["filename"], d / "pages" / fn)
            pages_meta.append(
                {"page_number": next_no, "filename": fn, "width": meta["width"], "height": meta["height"], "kind": "image", "label": src.get("label") or src["filename"]}
            )
            next_no += 1

        m = storage.read_manifest(document_id)
        m["page_count"] = len(pages_meta)
        m["progress"]["pages_total"] = len(pages_meta)
        storage.write_manifest(m)
        await _emit(document_id, {"type": "progress", "progress": m["progress"]})

        for pm in pages_meta:
            m = storage.read_manifest(document_id)
            m["pages"].append(
                {
                    "page_number": pm["page_number"],
                    "kind": pm["kind"],
                    "label": pm["label"],
                    "image": f"pages/{pm['filename']}",
                    "width": pm["width"],
                    "height": pm["height"],
                    "summary": "",
                    "status": "pending",  # pending | skipped | analyzed
                    "gate": None,
                    "has_charts": False,
                    "chart_ids": [],
                }
            )
            m["progress"]["pages_rendered"] += 1
            storage.write_manifest(m)
            await _emit(
                document_id,
                {"type": "page_rendered", "page_number": pm["page_number"], "kind": pm["kind"], "label": pm["label"],
                 "image": f"pages/{pm['filename']}", "width": pm["width"], "height": pm["height"]},
            )
            await _emit(document_id, {"type": "progress", "progress": m["progress"]})

        # -------- 2. Gate, then analyze (sequential for continuity notes) --------
        gate_on = settings.gate_enabled and bool(settings.gate_model)
        msg = f"Screening pages with {settings.gate_model}, extracting with {settings.openai_model}" if gate_on else f"Analyzing pages with {settings.openai_model}"
        await _emit(document_id, {"type": "status", "status": "analyzing", "message": msg})
        storage.set_status(document_id, "analyzing", msg)

        running_notes = ""
        for pm in pages_meta:
            page_no = pm["page_number"]
            label = pm["label"]
            use_gate = gate_on and (pm["kind"] == "pdf" or settings.gate_images)

            if use_gate:
                await _emit(document_id, {"type": "status", "status": "analyzing", "message": f"Screening {label} with {settings.gate_model}"})
                t0 = time.perf_counter()
                verdict, gusage = await asyncio.to_thread(gate.classify_page, d / "pages" / pm["filename"])
                gusage["seconds"] = round(time.perf_counter() - t0, 2)

                m = storage.read_manifest(document_id)
                page_entry = _find_page(m, page_no)
                gblock = costs.ensure_gate_block(m, settings.gate_model)
                gusage["cost_usd"] = costs.cost_usd(gusage, gblock["pricing"])
                costs.add_to_totals(gblock["totals"], gusage)
                p_charts = gate.chart_probability(verdict)
                skip = p_charts < settings.gate_threshold
                page_entry["gate"] = {**verdict.model_dump(), "p_charts": round(p_charts, 3), "skipped": skip, "usage": gusage}
                if skip:
                    page_entry["status"] = "skipped"
                    page_entry["summary"] = f"Skipped by gate: {verdict.reason}"
                    m["progress"]["pages_analyzed"] += 1
                    m["progress"]["pages_skipped"] = m["progress"].get("pages_skipped", 0) + 1
                storage.write_manifest(m)

                await _emit(document_id, {"type": "usage", "scope": "gate", "page_number": page_no, "usage": gusage,
                                          "totals": gblock["totals"], "model": gblock["model"], "pricing": gblock["pricing"]})
                if skip:
                    await _log(document_id, f"{label}: no charts (gate {p_charts:.0%} likely) — skipped, {settings.openai_model} not called. {verdict.reason}")
                    await _emit(document_id, {"type": "page_skipped", "page_number": page_no, "reason": verdict.reason, "confidence": verdict.confidence})
                    await _emit(document_id, {"type": "progress", "progress": m["progress"]})
                    continue
                await _log(document_id, f"{label}: gate sees {verdict.chart_count} chart(s) ({p_charts:.0%}) → extracting with {settings.openai_model}")
            elif pm["kind"] == "image":
                await _log(document_id, f"{label}: uploaded image — sent straight to {settings.openai_model}")

            running_notes = await analyze_page(document_id, page_no, running_notes)

        # -------- 3. Done --------
        m = storage.read_manifest(document_id)
        skipped = m["progress"].get("pages_skipped", 0)
        done_msg = f"Done — {len(m['charts'])} charts extracted" + (f", {skipped} page(s) skipped by the gate" if skipped else "")
        storage.set_status(document_id, "complete", done_msg)
        await _emit(document_id, {"type": "status", "status": "complete", "message": done_msg})
        await _emit(document_id, {"type": "complete", "charts_count": len(m["charts"])})

    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        storage.set_status(document_id, "error", "Processing failed", error=str(exc))
        await _emit(document_id, {"type": "error", "message": str(exc)})


# --------------------------------------------------------------------------- #
# One page through the extraction model
# --------------------------------------------------------------------------- #
async def analyze_page(document_id: str, page_no: int, running_notes: str = "") -> str:
    """Full extraction for one page: charts, crops, versions, usage, events.
    Returns the updated running notes for the next page."""
    settings = get_settings()
    d = storage.doc_dir(document_id)
    m = storage.read_manifest(document_id)
    page_entry = _find_page(m, page_no)
    label = _label(page_entry)
    page_png = d / page_entry["image"]
    total = m.get("page_count") or len(m["pages"])

    await _emit(document_id, {"type": "status", "status": "analyzing", "message": f"Reading {label} ({page_no} of {total})"})
    await _log(document_id, f"Sending {label} to {settings.openai_model} ({settings.reasoning_effort} reasoning)…")

    t0 = time.perf_counter()
    extraction, usage = await asyncio.to_thread(extract_page, page_png, page_no, running_notes)
    usage["seconds"] = round(time.perf_counter() - t0, 2)
    new_notes = (extraction.updated_running_notes or running_notes)[:2000]

    m = storage.read_manifest(document_id)
    page_entry = _find_page(m, page_no)
    page_entry["summary"] = extraction.summary
    page_entry["has_charts"] = bool(extraction.charts)
    page_entry["status"] = "analyzed"
    m["progress"]["pages_analyzed"] += 1
    # token/cost accounting for this page (API-billed counts x list price)
    ublock = costs.ensure_usage_block(m, settings.openai_model)
    usage["cost_usd"] = costs.cost_usd(usage, ublock["pricing"])
    page_entry["usage"] = usage
    costs.add_to_totals(ublock["totals"], usage)

    created_charts: list[dict] = []
    existing_ids = {c["chart_id"] for c in m["charts"]}
    idx = 0
    for ec in extraction.charts:
        idx += 1
        chart_id = f"p{page_no:03d}_c{idx:02d}"
        while chart_id in existing_ids:  # re-analysis of a page: never reuse an id
            idx += 1
            chart_id = f"p{page_no:03d}_c{idx:02d}"
        existing_ids.add(chart_id)
        m["progress"]["charts_detected"] += 1

        # crop original region for side-by-side comparison
        crop_meta = await asyncio.to_thread(pdf_processor.crop_chart, page_png, ec.bbox, d / "charts" / f"{chart_id}.png")
        crop_rel = f"charts/{chart_id}.png"
        await _emit(document_id, {"type": "chart_detected", "chart_id": chart_id, "page_number": page_no, "crop_image": crop_rel})

        spec = _spec_from_extracted(ec, crop_box=crop_meta.get("box_norm"))
        # measure the original's stroke weight / text size from the crop (no model call)
        spec["style_metrics"] = await asyncio.to_thread(style_metrics.measure, d / "charts" / f"{chart_id}.png", spec.get("plot_rect"), spec)
        chart_obj = {
            "chart_id": chart_id,
            "page_number": page_no,
            "crop_image": crop_rel,
            "bbox": ec.bbox,
            "confidence": ec.confidence.model_dump(),
            "status": "extracted",
            "current_version": 0,
            "final_version": None,
            "versions": [{"version": 0, "label": "Original extraction", "kind": "original", "created_at": storage._now(), "spec": spec}],
        }
        m["charts"].append(chart_obj)
        page_entry["chart_ids"].append(chart_id)
        created_charts.append(chart_obj)
        m["progress"]["charts_extracted"] += 1
        await _emit(document_id, {"type": "chart_extracted", "chart": chart_obj})
        await _emit(document_id, {"type": "progress", "progress": m["progress"]})

    m["charts_count"] = len(m["charts"])
    storage.write_manifest(m)
    await _emit(document_id, {"type": "usage", "scope": "extract", "page_number": page_no, "usage": usage,
                              "totals": ublock["totals"], "model": ublock["model"], "pricing": ublock["pricing"]})
    await _log(document_id, f"{label}: {usage['input_tokens']:,} in / {usage['output_tokens']:,} out tokens → ${usage['cost_usd']:.4f} in {usage['seconds']}s")

    xml = _page_xml(page_no, extraction.summary, created_charts)
    (d / f"page_{page_no:03d}.txt").write_text(f"<document_page>\n{xml}\n</document_page>\n", encoding="utf-8")

    await _emit(document_id, {"type": "page_analyzed", "page_number": page_no, "summary": extraction.summary, "charts_found": len(created_charts)})
    await _emit(document_id, {"type": "progress", "progress": m["progress"]})
    return new_notes


async def analyze_page_now(document_id: str, page_no: int) -> dict[str, Any]:
    """Manual override ("Process anyway") for a page the gate skipped. Returns the manifest."""
    m = storage.read_manifest(document_id)
    page_entry = _find_page(m, page_no)
    if page_entry.get("status") == "skipped":
        m["progress"]["pages_skipped"] = max(0, m["progress"].get("pages_skipped", 0) - 1)
        m["progress"]["pages_analyzed"] = max(0, m["progress"].get("pages_analyzed", 0) - 1)
        if isinstance(page_entry.get("gate"), dict):
            page_entry["gate"]["skipped"] = False
            page_entry["gate"]["overridden"] = True
    page_entry["status"] = "pending"
    storage.write_manifest(m)

    await analyze_page(document_id, page_no, "")

    m = storage.read_manifest(document_id)
    skipped = m["progress"].get("pages_skipped", 0)
    done_msg = f"Done — {len(m['charts'])} charts extracted" + (f", {skipped} page(s) skipped by the gate" if skipped else "")
    storage.set_status(document_id, "complete", done_msg)
    return storage.read_manifest(document_id)


# --------------------------------------------------------------------------- #
# Spec helpers
# --------------------------------------------------------------------------- #
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
