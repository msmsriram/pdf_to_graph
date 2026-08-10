"""Wrapper around gpt-5.6-sol for page understanding + chart extraction.

Uses the Responses API with Structured Outputs (`responses.parse` +
`text_format=PageExtraction`) so we get a validated Pydantic object back with no
brittle string parsing. Falls back to a plain JSON request if `parse` is
unavailable for any reason.

This is a synchronous, blocking call — the pipeline runs it via
`asyncio.to_thread` so the event loop (and the WebSocket feed) stays responsive.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from openai import OpenAI

from .config import get_settings
from .schemas import PageExtraction

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set. Copy backend/.env.example to backend/.env and fill it in.")
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


def _encode_image(path: Path) -> str:
    b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:image/png;base64,{b64}"


SYSTEM_PROMPT = """You are a meticulous scientific chart-extraction engine.
You are given ONE page image from a PDF and rolling context notes from previous pages.

Your job:
1. Write a concise `summary` of the page.
2. Detect EVERY chart/graph/plot on the page (line, bar, scatter, pie, area).
   - Do NOT treat plain data tables, logos, photos, schematics, or equations as charts.
   - A single page may contain zero, one, or many charts (dashboards are common).
3. For each chart, extract structured, editable data:
   - title/subtitle, chart_type, whether it is stacked.
   - x_axis and y_axis: label, unit, scale (linear/log), and numeric min/max when visible.
   - series: one per curve. Set `name` to the curve's ON-PLOT label if it is labelled directly
     next to the line (e.g. '6V', '5V', 'VGS=7~10V'); otherwise use its legend name. Include
     `conditions` (e.g. 'VCE=-1V'), color if readable, and data points. Use x/y for numeric charts
     and `label` for bar/pie categories.
   - Reproduce the ORIGINAL's appearance, not a generic chart:
     * inline_labels = true if each curve is labelled directly on the plot (near its end); false if
       the chart uses a separate legend box.
     * legend = true ONLY if the original draws a legend box; false when curves are labelled inline.
     * show_markers = true ONLY if the original draws visible point markers (dots) on the curves;
       datasheet line charts usually have NONE, so default false.
     * annotations = every in-plot text box (e.g. a 'Note: 1.TA=25C 2.Pulse test' block). Give its
       text (keep line breaks) and its corner position. Do NOT put axis titles or curve labels here.
   - Values you read off a plotted curve are ESTIMATES. Read printed data labels and any
     accompanying table FIRST — those are exact; only estimate from pixels when nothing is printed.
   - bbox: a normalized [x0,y0,x1,y1] (0..1) tightly around the chart, for cropping.
   - confidence: honest 0..1 scores for overall/axis/legend/data.
4. Use the rolling notes for continuity: legends, abbreviations, or a chart continued from a
   previous page. IGNORE irrelevant carried-over content — only keep what helps THIS page.
5. Return `updated_running_notes`: a COMPACT (a few lines) set of notes to carry forward
   (key abbreviations, units, legend/color definitions, entities). Do not let it grow unbounded.

Be accurate and conservative. If you are unsure whether something is a chart, lower its confidence
rather than inventing data."""


def extract_page(page_png: Path, page_number: int, running_notes: str) -> PageExtraction:
    settings = get_settings()
    client = _get_client()

    user_text = (
        f"Page number: {page_number}.\n"
        f"Rolling notes from previous pages (may be empty):\n\"\"\"\n{running_notes}\n\"\"\"\n\n"
        f"Extract this page now."
    )
    content = [
        {"type": "input_text", "text": user_text},
        {
            "type": "input_image",
            "image_url": _encode_image(page_png),
            "detail": settings.image_detail,
        },
    ]
    messages = [{"role": "user", "content": content}]

    # Primary path: strict structured output. System prompt via `instructions`.
    try:
        resp = client.responses.parse(
            model=settings.openai_model,
            reasoning={"effort": settings.reasoning_effort},
            instructions=SYSTEM_PROMPT,
            input=messages,
            text_format=PageExtraction,
        )
        parsed = resp.output_parsed
        if parsed is not None:
            parsed.page_number = page_number
            return parsed
    except Exception as exc:  # noqa: BLE001 — fall back below, but surface the reason.
        print(f"[openai] responses.parse failed on page {page_number}: {exc!r}; trying JSON fallback")

    # Fallback path: ask for raw JSON and validate ourselves.
    resp = client.responses.create(
        model=settings.openai_model,
        reasoning={"effort": settings.reasoning_effort},
        instructions=SYSTEM_PROMPT + "\n\nReturn ONLY valid JSON matching the schema.",
        input=messages,
    )
    raw = resp.output_text or "{}"
    data = _loads_lenient(raw)
    data["page_number"] = page_number
    return PageExtraction.model_validate(data)


def _loads_lenient(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lstrip().lower().startswith("json"):
            raw = raw.lstrip()[4:]
    try:
        return json.loads(raw)
    except Exception:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(raw[start : end + 1])
        return {"page_number": 0, "summary": raw[:500], "has_charts": False, "charts": [], "updated_running_notes": ""}
