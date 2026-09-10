"""Token accounting and USD cost computation.

Cost is computed from the API's own `usage` counts (authoritative) times the
model's list price. Reasoning tokens are billed as output tokens (they are already
included in `output_tokens`); cached input tokens are billed at the cached rate.

Prices are USD per 1M tokens on the standard tier.
- Per-model overrides / additions: PRICING_JSON env, e.g.
  {"gpt-5.4-mini": {"input": 0.4, "cached_input": 0.04, "output": 1.6}}
- PRICE_INPUT_PER_M / PRICE_CACHED_INPUT_PER_M / PRICE_OUTPUT_PER_M apply only to
  models that are not in the table or PRICING_JSON (a generic fallback).
Unknown models report known=False: tokens are still counted, cost shows as 0.
"""
from __future__ import annotations

import json
import os
from typing import Any

PRICING: dict[str, dict[str, float]] = {
    "gpt-6-astra": {"input": 10.0, "cached_input": 1.0, "output": 50.0},
    "gpt-5.6-sol": {"input": 5.0, "cached_input": 0.5, "output": 30.0},
}


def _env_pricing() -> dict[str, dict[str, float]]:
    raw = os.environ.get("PRICING_JSON", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    except ValueError:
        return {}


def pricing_for(model: str) -> dict[str, Any]:
    base = dict(PRICING.get(model) or {})
    base.update(_env_pricing().get(model) or {})
    known = bool(base)
    p = {"input": 0.0, "cached_input": 0.0, "output": 0.0}
    if known:
        p.update({k: float(base.get(k, 0.0)) for k in p})
    else:
        for key, env in (("input", "PRICE_INPUT_PER_M"), ("cached_input", "PRICE_CACHED_INPUT_PER_M"), ("output", "PRICE_OUTPUT_PER_M")):
            v = os.environ.get(env)
            if v:
                try:
                    p[key] = float(v)
                    known = True
                except ValueError:
                    pass
    p["known"] = known
    p["unit"] = "USD per 1M tokens"
    return p


def cost_usd(usage: dict[str, Any], pricing: dict[str, Any]) -> float:
    inp = int(usage.get("input_tokens") or 0)
    cached = min(int(usage.get("cached_tokens") or 0), inp)
    out = int(usage.get("output_tokens") or 0)
    dollars = (
        (inp - cached) * float(pricing.get("input", 0)) + cached * float(pricing.get("cached_input", 0)) + out * float(pricing.get("output", 0))
    ) / 1_000_000
    return round(dollars, 6)


def empty_totals() -> dict[str, Any]:
    return {"calls": 0, "input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0, "seconds": 0.0, "cost_usd": 0.0}


def add_to_totals(totals: dict[str, Any], usage: dict[str, Any]) -> dict[str, Any]:
    totals["calls"] = int(totals.get("calls", 0)) + 1
    for k in ("input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens", "total_tokens"):
        totals[k] = int(totals.get(k, 0)) + int(usage.get(k) or 0)
    totals["seconds"] = round(float(totals.get("seconds", 0.0)) + float(usage.get("seconds") or 0.0), 2)
    totals["cost_usd"] = round(float(totals.get("cost_usd", 0.0)) + float(usage.get("cost_usd") or 0.0), 6)
    return totals


def ensure_usage_block(manifest: dict[str, Any], model: str) -> dict[str, Any]:
    """Extraction-model accounting (per page + re-runs)."""
    u = manifest.get("usage")
    if not isinstance(u, dict):
        u = {}
        manifest["usage"] = u
    u.setdefault("model", model)
    u.setdefault("pricing", pricing_for(model))
    u.setdefault("totals", empty_totals())
    u.setdefault("reruns", [])
    return u


def ensure_gate_block(manifest: dict[str, Any], model: str) -> dict[str, Any]:
    """Gate-model accounting, kept separate so its saving is visible."""
    u = manifest.get("usage")
    if not isinstance(u, dict):
        u = {}
        manifest["usage"] = u
    g = u.get("gate")
    if not isinstance(g, dict):
        g = {}
        u["gate"] = g
    g.setdefault("model", model)
    g.setdefault("pricing", pricing_for(model))
    g.setdefault("totals", empty_totals())
    return g
