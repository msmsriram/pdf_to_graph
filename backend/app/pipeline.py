"""Document processing pipeline.

Runs as a background asyncio task. Heavy/blocking work (PDF render, OpenAI call,
image crop) is pushed to threads so the event loop keeps flushing WebSocket
events. Every meaningful step emits an event on the bus for the live UI.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from . import pdf_processor, storage
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
            import json as _json

            lines.append(f'    <chart id="{c["chart_id"]}" type="{spec.get("chart_type")}">')
            lines.append(f"      {_json.dumps(spec, ensure_ascii=False)}")
            lines.append("    </chart>")
        lines.append("  </charts>")
    lines.append("</page>")
    return "\n".join(lines)


async def process_document(document_id: str) -> None:
    settings = get_settings()
    d = storage.doc_dir(document_id)
    pdf_path = d / "original.pdf"

    try:
        # -------- 1. Render --------
        await _emit(document_id, {"type": "status", "status": "rendering", "message": "Rendering PDF pages to images"})
        storage.set_status(document_id, "rendering", "Rendering PDF pages to images")

        total = pdf_processor.page_count(pdf_path)
        capped = min(total, settings.max_pages)
        if total > settings.max_pages:
            await _log(document_id, f"PDF has {total} pages; processing first {settings.max_pages} (MAX_PAGES cap).", "warn")

        m = storage.read_manifest(document_id)
        m["page_count"] = capped
        m["progress"]["pages_total"] = capped
        storage.write_manifest(m)
        await _emit(document_id, {"type": "progress", "progress": m["progress"]})

        pages_meta = await asyncio.to_thread(
            pdf_processor.render_pdf_to_pngs, pdf_path, d / "pages", settings.render_dpi, settings.max_pages
        )

        m = storage.read_manifest(document_id)
        for pm in pages_meta:
            m["pages"].append(
                {
                    "page_number": pm["page_number"],
                    "image": f"pages/{pm['filename']}",
                    "width": pm["width"],
                    "height": pm["height"],
                    "summary": "",
                    "has_charts": False,
                    "chart_ids": [],
                }
            )
            m["progress"]["pages_rendered"] += 1
            storage.write_manifest(m)
            await _emit(
                document_id,
                {
                    "type": "page_rendered",
                    "page_number": pm["page_number"],
                    "image": f"pages/{pm['filename']}",
                    "width": pm["width"],
                    "height": pm["height"],
                },
            )
            await _emit(document_id, {"type": "progress", "progress": m["progress"]})

        # -------- 2. Analyze each page (sequential for continuity) --------
        await _emit(document_id, {"type": "status", "status": "analyzing", "message": "Analyzing pages with gpt-5.6-sol"})
        storage.set_status(document_id, "analyzing", "Analyzing pages with gpt-5.6-sol")

        running_notes = ""
        for pm in pages_meta:
            page_no = pm["page_number"]
            page_png = d / "pages" / pm["filename"]
            await _emit(document_id, {"type": "status", "status": "analyzing",
                                      "message": f"Reading page {page_no} of {capped}"})
            await _log(document_id, f"Sending page {page_no} to gpt-5.6-sol ({settings.reasoning_effort} reasoning)…")

            extraction = await asyncio.to_thread(extract_page, page_png, page_no, running_notes)
            running_notes = (extraction.updated_running_notes or running_notes)[:2000]

            m = storage.read_manifest(document_id)
            page_entry = next(p for p in m["pages"] if p["page_number"] == page_no)
            page_entry["summary"] = extraction.summary
            page_entry["has_charts"] = bool(extraction.charts)
            m["progress"]["pages_analyzed"] += 1

            created_charts: list[dict] = []
            for idx, ec in enumerate(extraction.charts, start=1):
                chart_id = f"p{page_no:03d}_c{idx:02d}"
                m["progress"]["charts_detected"] += 1

                # crop original region for side-by-side comparison
                crop_meta = await asyncio.to_thread(
                    pdf_processor.crop_chart, page_png, ec.bbox, d / "charts" / f"{chart_id}.png"
                )
                crop_rel = f"charts/{chart_id}.png"

                await _emit(
                    document_id,
                    {"type": "chart_detected", "chart_id": chart_id, "page_number": page_no, "crop_image": crop_rel},
                )

                spec = _spec_from_extracted(ec)
                chart_obj = {
                    "chart_id": chart_id,
                    "page_number": page_no,
                    "crop_image": crop_rel,
                    "bbox": ec.bbox,
                    "confidence": ec.confidence.model_dump(),
                    "status": "extracted",
                    "current_version": 0,
                    "final_version": None,
                    "versions": [
                        {
                            "version": 0,
                            "label": "Original extraction",
                            "kind": "original",
                            "created_at": storage._now(),
                            "spec": spec,
                        }
                    ],
                }
                m["charts"].append(chart_obj)
                page_entry["chart_ids"].append(chart_id)
                created_charts.append(chart_obj)
                m["progress"]["charts_extracted"] += 1

                await _emit(document_id, {"type": "chart_extracted", "chart": chart_obj})
                await _emit(document_id, {"type": "progress", "progress": m["progress"]})

            m["charts_count"] = len(m["charts"])
            storage.write_manifest(m)

            # write per-page XML artifact
            xml = _page_xml(page_no, extraction.summary, created_charts)
            (d / f"page_{page_no:03d}.txt").write_text(
                f"<document_page>\n{xml}\n</document_page>\n", encoding="utf-8"
            )

            await _emit(document_id, {"type": "page_analyzed", "page_number": page_no,
                                      "summary": extraction.summary, "charts_found": len(created_charts)})
            await _emit(document_id, {"type": "progress", "progress": m["progress"]})

        # -------- 3. Done --------
        storage.set_status(document_id, "complete", f"Done — {len(m['charts'])} charts extracted")
        await _emit(document_id, {"type": "status", "status": "complete",
                                  "message": f"Done — {len(m['charts'])} charts extracted"})
        await _emit(document_id, {"type": "complete", "charts_count": len(m["charts"])})

    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        storage.set_status(document_id, "error", "Processing failed", error=str(exc))
        await _emit(document_id, {"type": "error", "message": str(exc)})


def _spec_from_extracted(ec: ExtractedChart) -> dict:
    """Convert the model's ExtractedChart into the stored/editable ChartSpec dict."""
    return {
        "title": ec.title,
        "subtitle": ec.subtitle,
        "chart_type": ec.chart_type,
        "stacked": ec.stacked,
        "x_axis": ec.x_axis.model_dump(),
        "y_axis": ec.y_axis.model_dump(),
        "series": [s.model_dump() for s in ec.series],
        "legend": ec.legend,
        "inline_labels": ec.inline_labels,
        "show_markers": ec.show_markers,
        "annotations": [a.model_dump() for a in ec.annotations],
        "notes": ec.notes,
        "confidence": ec.confidence.model_dump(),
    }
