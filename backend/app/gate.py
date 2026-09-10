"""Chart gate: a cheap vision model decides whether a page has any data chart before
the expensive extraction model is called.

Validated on the sample datasheets: gpt-5.4-mini at low image detail classified
8/8 pages correctly (and counted the charts) at ~840 input tokens per page.
Fails OPEN: any error or missing answer counts as "may have charts", so a gate
failure can only cost one extraction call, never a chart.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

from .config import get_settings
from .openai_client import _get_client, _usage_from
from .schemas import GateResult

GATE_PROMPT = (
    "You are a page classifier for a chart-extraction pipeline. Decide whether this page image "
    "contains at least one DATA CHART: a line, bar, scatter, area or pie plot with axes/ticks or plotted "
    "values (e.g. characteristic curves, SOA plots, derating curves). Do NOT count: tables, logos, "
    "pinout/package outline drawings, circuit schematics, block diagrams, flowcharts, photos, or text. "
    "Return has_charts, chart_count, confidence (0..1) and a one-sentence reason."
)


def _small_png_b64(path: Path, max_side: int = 1024) -> str:
    """Downscaled PNG for the gate call (the model looks at ~512px anyway; keeps the payload small)."""
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _zero_usage(model: str, effort: str) -> dict:
    return {"model": model, "effort": effort, "input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0}


def classify_page(page_png: Path) -> tuple[GateResult, dict]:
    """Returns (verdict, usage). Never raises — fails open."""
    s = get_settings()
    try:
        client = _get_client()
        b64 = _small_png_b64(page_png)
        resp = client.responses.parse(
            model=s.gate_model,
            reasoning={"effort": s.gate_reasoning},
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": GATE_PROMPT},
                        {"type": "input_image", "image_url": f"data:image/png;base64,{b64}", "detail": s.gate_image_detail},
                    ],
                }
            ],
            text_format=GateResult,
        )
        usage = _usage_from(resp, s.gate_model, s.gate_reasoning)
        parsed = resp.output_parsed
        if parsed is None:
            return GateResult(has_charts=True, chart_count=0, confidence=0.0, reason="Gate returned no answer; passing page through."), usage
        parsed.confidence = max(0.0, min(1.0, float(parsed.confidence)))
        return parsed, usage
    except Exception as exc:  # noqa: BLE001 — fail open
        return (
            GateResult(has_charts=True, chart_count=0, confidence=0.0, reason=f"Gate error ({type(exc).__name__}); passing page through."),
            _zero_usage(s.gate_model, s.gate_reasoning),
        )


def chart_probability(g: GateResult) -> float:
    """P(page has charts) from the verdict + its confidence."""
    return g.confidence if g.has_charts else 1.0 - g.confidence
