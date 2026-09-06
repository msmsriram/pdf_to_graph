"""Wrapper around the OpenAI vision model (OPENAI_MODEL, default gpt-6-astra) for page
understanding + chart extraction.

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
from .schemas import ExtractedChart, PageExtraction

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


def _usage_from(resp, model: str, effort: str) -> dict:
    """Read the API's billed token counts off a Responses object (all fields optional)."""
    u = getattr(resp, "usage", None)

    def g(obj, name: str, default: int = 0) -> int:
        v = getattr(obj, name, None) if obj is not None else None
        return int(v) if isinstance(v, (int, float)) else default

    inp = g(u, "input_tokens")
    out = g(u, "output_tokens")
    return {
        "model": model,
        "effort": effort,
        "input_tokens": inp,
        "cached_tokens": g(getattr(u, "input_tokens_details", None), "cached_tokens"),
        "output_tokens": out,
        "reasoning_tokens": g(getattr(u, "output_tokens_details", None), "reasoning_tokens"),
        "total_tokens": g(u, "total_tokens", inp + out),
    }


SYSTEM_PROMPT = """You are a meticulous scientific chart-extraction engine.
You are given ONE page image from a PDF and rolling context notes from previous pages.

Your job:
1. Write a concise `summary` of the page.
2. Detect EVERY chart/graph/plot on the page (line, bar, scatter, pie, area).
   - Do NOT treat plain data tables, logos, photos, schematics, or equations as charts.
   - A single page may contain zero, one, or many charts (dashboards are common).
3. For each chart, extract structured, editable data:
   - title/subtitle, chart_type, whether it is stacked.
   - x_axis and y_axis: label, unit, scale (linear/log), numeric min/max when visible, and the tick
     spacing: major_interval = the step between labelled ticks (0,2,4,… → 2; 0,5,10,… → 5);
     minor_interval = the step of the finer unlabelled gridlines if the plot has them, else null.
     For log axes leave both null (decades are implied).
     AXIS DIRECTION: set inverse = true when the printed values DECREASE along the axis direction —
     going UP for the y-axis or going RIGHT for the x-axis. Typical case: a y-axis reading 0 at the
     bottom and -0.2, -0.4 … -1.0 towards the top (PNP transistors, negative quantities). Otherwise
     false. min/max are ALWAYS the numerically smaller/larger end values regardless of direction.
   - series: one per curve. Set `name` to the curve's ON-PLOT label if it is labelled directly
     next to the line (e.g. '6V', '5V', 'VGS=7~10V'); otherwise use its legend name. Include
     `conditions` (e.g. 'VCE=-1V'), color if readable, and data points. Use x/y for numeric charts
     and `label` for bar/pie categories. For each curve also give label_x/label_y = the DATA
     coordinates of the CENTRE of its printed on-plot label (null if the chart uses a legend box),
     and label_boxed = true when that label sits inside a bordered box.
     LINE WEIGHT: give line_width per curve by comparing its stroke to the chart's own axis lines:
     'thin' = about as fine as the axis/grid lines, 'medium' = roughly twice that (the usual datasheet
     curve), 'thick' = bold, three times or more. Curves in one chart may differ (e.g. a bold limit line).
   - OVERLAPPING CURVES: lines drawn on top of, or a hair beside, each other are SEPARATE series.
     A label like 'VGS=7~10V' covers several gate voltages drawn as a bundle (often two colours side
     by side); two curves may coincide at low x and split later. Extract EVERY distinct line you can
     see — each distinct colour is its own series — and never merge a bundle into one curve. When
     bundled lines share one label, name them by colour or position (e.g. 'VGS=7~10V (upper, cyan)',
     'VGS=7~10V (lower, pink)'). Before finishing, re-count the coloured lines in the image and make
     sure the number of series matches.
   - POINT DENSITY: sample each curve with 25–40 points spread across its full x-range, adding extra
     points where it bends (knees, roll-offs, saturation) so a line through them reproduces the shape.
     Straight-line segments need only their end points. On log axes space samples evenly in LOG terms
     (e.g. 1, 2, 5, 10, 20, 50 …), never only at the decade ticks.
   - Reproduce the ORIGINAL's appearance, not a generic chart:
     * smooth = true for smooth characteristic curves; false for piecewise-linear plots drawn with
       straight segments (e.g. SOA boundaries).
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
   - plot_bbox: a normalized [x0,y0,x1,y1] of the PLOT AREA only — the rectangle bounded by the
     axes lines, excluding tick labels, axis titles and the chart title — in the same frame as bbox.
     Be precise: it is used to overlay the reconstruction on the original.
   - confidence: honest 0..1 scores for overall/axis/legend/data.
4. Use the rolling notes for continuity: legends, abbreviations, or a chart continued from a
   previous page. IGNORE irrelevant carried-over content — only keep what helps THIS page.
5. Return `updated_running_notes`: a COMPACT (a few lines) set of notes to carry forward
   (key abbreviations, units, legend/color definitions, entities). Do not let it grow unbounded.

Be accurate and conservative. If you are unsure whether something is a chart, lower its confidence
rather than inventing data."""


def extract_page(page_png: Path, page_number: int, running_notes: str) -> tuple[PageExtraction, dict]:
    """Returns (extraction, usage) — usage holds the API's billed token counts."""
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
            return parsed, _usage_from(resp, settings.openai_model, settings.reasoning_effort)
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
    return PageExtraction.model_validate(data), _usage_from(resp, settings.openai_model, settings.reasoning_effort)


SINGLE_CHART_ADDENDUM = """

RE-EXTRACTION MODE: the image is ONE chart cropped from a datasheet page. Extract exactly this chart
with maximum care — accurate axis ranges and tick steps, dense well-placed points that reproduce the
curve shapes, exact on-plot label positions, note boxes. Ignore anything above about page summaries
or rolling notes. Set bbox to [0, 0, 1, 1] and plot_bbox to the axes rectangle (plot area only)
within THIS image. A previous extraction is provided for reference only —
it may contain errors; always trust the image over it."""


def extract_single_chart(
    crop_png: Path, prior_spec: dict, effort: str | None = None, model: str | None = None
) -> tuple[ExtractedChart, dict]:
    """Re-extract a single chart from its crop, optionally on a stronger model/effort.
    Returns (extraction, usage)."""
    settings = get_settings()
    client = _get_client()
    use_model = model or settings.openai_model
    use_effort = effort or settings.reasoning_effort
    user_text = (
        "Re-extract this single chart.\n"
        f"Previous extraction (reference only, may be wrong):\n{json.dumps(prior_spec, ensure_ascii=False)[:6000]}"
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": user_text},
                {"type": "input_image", "image_url": _encode_image(crop_png), "detail": settings.image_detail},
            ],
        }
    ]
    resp = client.responses.parse(
        model=use_model,
        reasoning={"effort": use_effort},
        instructions=SYSTEM_PROMPT + SINGLE_CHART_ADDENDUM,
        input=messages,
        text_format=ExtractedChart,
    )
    parsed = resp.output_parsed
    if parsed is None:
        raise RuntimeError("Model returned no structured output for the chart.")
    return parsed, _usage_from(resp, use_model, use_effort)


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
