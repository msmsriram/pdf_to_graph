"""Measure the ORIGINAL chart's stroke weight and text size from its crop image.

The renderer needs proportions, not adjectives: "the curve stroke is 0.9% of the crop
height", "tick digits are 4.3% of the crop height". Measured once per chart with plain
numpy on the crop PNG — deterministic, no model call. Everything is returned as a
fraction of the crop height (the on-screen pane keeps the crop's aspect ratio, so
px = fraction × pane height at any size).

Method
- ink mask: pixels that are dark OR clearly coloured (curves may be blue/red/cyan).
- curve stroke (primary): we know where each curve is — its extracted points mapped
  through the plot rectangle and axis ranges — so along each curve we read the ink
  profile PERPENDICULAR to the local direction and take the run through the centre.
  Text and gridlines are ignored by construction, and each series gets its own width.
- curve stroke (fallback, no usable geometry): pixel-weighted median width of ink that
  is not a full-length axis-aligned line.
- grid width: thinnest common width among full-length horizontal/vertical ink lines.
- text: the first band of inked rows under the plot area is the x tick labels; its
  height is the digit (cap) height; font size ≈ cap height / 0.72.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
from PIL import Image

CAP_HEIGHT_RATIO = 0.72  # digit/cap height as a fraction of font size (Arial-like)
AA_ALLOWANCE = 0.5  # anti-aliasing adds roughly half a pixel of "ink" at our threshold


# ----------------------------------------------------------------------------- basics
def _ink_mask(rgb: np.ndarray) -> np.ndarray:
    r, g, b = rgb[..., 0].astype(np.int32), rgb[..., 1].astype(np.int32), rgb[..., 2].astype(np.int32)
    lum = (r * 299 + g * 587 + b * 114) // 1000
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0.0)
    return (lum < 170) | ((sat > 0.22) & (lum < 235))


def _runs(mask: np.ndarray, axis: int) -> np.ndarray:
    """Length of the run of True values each pixel belongs to, along `axis`."""
    m = mask if axis == 1 else mask.T
    out = np.zeros_like(m, dtype=np.int32)
    for r in range(m.shape[0]):
        row = m[r]
        if not row.any():
            continue
        d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
        for s, e in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)):
            out[r, s:e] = e - s
    return out if axis == 1 else out.T


def _detect_frame(ink: np.ndarray) -> Optional[list[float]]:
    """Plot-area rectangle from the long axis-aligned ink lines (frame / axes), normalized."""
    h, w = ink.shape
    rows = np.flatnonzero(ink.mean(axis=1) > 0.45)
    cols = np.flatnonzero(ink.mean(axis=0) > 0.45)
    if len(rows) < 1 or len(cols) < 1:
        return None
    y0, y1, x0, x1 = rows.min(), rows.max(), cols.min(), cols.max()
    if (y1 - y0) < 0.3 * h or (x1 - x0) < 0.3 * w:
        return None
    return [float(x0 / w), float(y0 / h), float((x1 + 1) / w), float((y1 + 1) / h)]


# --------------------------------------------------------------- curve-guided stroke
def _axis_t(ax: dict, vals: list[float]) -> Optional[Callable[[float], Optional[float]]]:
    """Map a data value to 0..1 along the axis (0 = min end), honouring log scale."""
    log = (ax or {}).get("scale") == "log"
    lo, hi = (ax or {}).get("min"), (ax or {}).get("max")
    good = [v for v in vals if v is not None and (not log or v > 0)]
    if lo is None:
        lo = min(good) if good else None
    if hi is None:
        hi = max(good) if good else None
    if lo is None or hi is None:
        return None
    f = (lambda v: math.log10(v) if v > 0 else None) if log else (lambda v: float(v))
    flo, fhi = f(lo), f(hi)
    if flo is None or fhi is None or fhi == flo:
        return None

    def t(v):
        fv = f(v)
        return None if fv is None else (fv - flo) / (fhi - flo)

    return t


def _curve_pixels(spec: dict, rect_px: tuple[int, int, int, int]) -> list[list[tuple[float, float]]]:
    """Each series' polyline in crop-pixel coordinates (empty list when not mappable)."""
    x0, y0, x1, y1 = rect_px
    pw, ph = x1 - x0, y1 - y0
    series = spec.get("series") or []
    xs = [p.get("x") for s in series for p in (s.get("points") or [])]
    ys = [p.get("y") for s in series for p in (s.get("points") or [])]
    tx = _axis_t(spec.get("x_axis") or {}, xs)
    ty = _axis_t(spec.get("y_axis") or {}, ys)
    if tx is None or ty is None:
        return []
    xinv = bool((spec.get("x_axis") or {}).get("inverse"))
    yinv = bool((spec.get("y_axis") or {}).get("inverse"))
    out: list[list[tuple[float, float]]] = []
    for s in series:
        poly: list[tuple[float, float]] = []
        for p in s.get("points") or []:
            if p.get("x") is None or p.get("y") is None:
                continue
            u, v = tx(p["x"]), ty(p["y"])
            if u is None or v is None or not (-0.05 <= u <= 1.05) or not (-0.05 <= v <= 1.05):
                continue
            cx = (x1 - u * pw) if xinv else (x0 + u * pw)
            cy = (y0 + v * ph) if yinv else (y1 - v * ph)
            poly.append((cx, cy))
        out.append(poly)
    return out


def _stroke_along(ink: np.ndarray, poly: list[tuple[float, float]], radius: int = 14) -> Optional[float]:
    """Median ink run-length perpendicular to the curve, sampled every ~4 px along it."""
    H, W = ink.shape
    steps = np.arange(-radius, radius + 0.5, 0.5)
    centre = int(np.argmin(np.abs(steps)))
    widths: list[float] = []
    tries = 0
    for (ax_, ay), (bx, by) in zip(poly[:-1], poly[1:]):
        dx, dy = bx - ax_, by - ay
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            continue
        nx, ny = -dy / dist, dx / dist  # unit normal
        n = max(2, int(dist / 4))
        for i in range(n):
            cx, cy = ax_ + dx * i / n, ay + dy * i / n
            xi = np.rint(cx + steps * nx).astype(int)
            yi = np.rint(cy + steps * ny).astype(int)
            ok = (xi >= 0) & (xi < W) & (yi >= 0) & (yi < H)
            if not ok.all():
                continue
            prof = ink[yi, xi]
            tries += 1
            # The WIDEST ink run within ±8 px of where the curve should be. The mapping can
            # be a few px off (model plot-rect, sampling), and the nearest run is then often
            # a thin gridline — gridlines are always thinner than curves, so prefer width.
            d = np.diff(np.concatenate(([0], prof.astype(np.int8), [0])))
            starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
            best = None
            for s, e in zip(starts, ends):
                gap = 0 if s <= centre < e else min(abs(s - centre), abs(e - 1 - centre))
                if gap > 16:  # 8 px
                    continue
                if best is None or (e - s) > (best[1] - best[0]):
                    best = (s, e)
            if best is None:
                continue
            widths.append((best[1] - best[0]) * 0.5)
    if tries == 0 or len(widths) < 10 or len(widths) < 0.3 * tries:
        return None
    # Trimmed mean of the middle 60%: drops the top (curve merging with a parallel gridline
    # or another curve) and the bottom (near-misses), and — unlike a median — gives a fair
    # compromise when a curve is drawn thinner in its flat parts than in its steep parts.
    arr = np.sort(np.asarray(widths))
    lo, hi = int(len(arr) * 0.2), max(int(len(arr) * 0.2) + 1, int(len(arr) * 0.8))
    return float(np.mean(arr[lo:hi]))


# ---------------------------------------------------------------------------- measure
def measure(crop_path: Path, plot_rect: Optional[list[float]] = None, spec: Optional[dict] = None) -> Optional[dict[str, Any]]:
    try:
        img = Image.open(crop_path).convert("RGB")
    except Exception:  # noqa: BLE001
        return None
    rgb = np.asarray(img)
    H, W = rgb.shape[:2]
    if H < 40 or W < 40:
        return None
    ink = _ink_mask(rgb)

    detected = _detect_frame(ink)
    rect = plot_rect if (plot_rect and len(plot_rect) == 4) else detected
    rect_source = "model" if (plot_rect and len(plot_rect) == 4) else ("detected" if detected else "default")
    if not rect:
        rect = [0.12, 0.08, 0.97, 0.85]
    x0, y0, x1, y1 = (int(round(rect[0] * W)), int(round(rect[1] * H)), int(round(rect[2] * W)), int(round(rect[3] * H)))
    if x1 - x0 < 30 or y1 - y0 < 30:
        return None

    # ---- per-series stroke, guided by the extracted curves ----
    series_w: list[Optional[float]] = []
    if spec:
        for poly in _curve_pixels(spec, (x0, y0, x1, y1)):
            w = _stroke_along(ink, poly) if len(poly) >= 2 else None
            series_w.append(None if w is None else max(1.0, w - AA_ALLOWANCE))
    good = [w for w in series_w if w is not None]
    curve_w: Optional[float] = float(np.median(good)) if good else None
    stroke_source = "curves" if good else "fallback"

    # ---- global fallback + grid width from the plot interior ----
    ix, iy = max(2, int(0.02 * (x1 - x0))), max(2, int(0.02 * (y1 - y0)))
    sub = ink[y0 + iy : y1 - iy, x0 + ix : x1 - ix]
    grid_w: Optional[float] = None
    if sub.shape[0] > 20 and sub.shape[1] > 20 and sub.any():
        ph, pw = sub.shape
        hr, vr = _runs(sub, 1), _runs(sub, 0)
        width = np.minimum(hr, vr)
        full_line = sub & ((hr > 0.85 * pw) | (vr > 0.85 * ph))
        if full_line.any():
            counts = np.bincount(width[full_line])
            if counts.size > 1:
                thresh = 0.2 * counts[1:].max()
                cands = [i for i in range(1, counts.size) if counts[i] >= thresh]
                if cands:
                    grid_w = max(0.5, cands[0] - AA_ALLOWANCE)
        if curve_w is None:
            rest = sub & ~full_line & (width >= 1)
            if rest.any():
                curve_w = max(1.0, float(np.median(width[rest])) - AA_ALLOWANCE)
    if grid_w is not None and curve_w is not None:
        grid_w = min(grid_w, curve_w)

    # ---- text: first inked band under the plot area (x tick labels) ----
    font_px: Optional[float] = None
    below = ink[min(H - 1, y1 + max(2, int(0.01 * H))) :, x0:x1]
    if below.shape[0] > 4:
        prof = below.mean(axis=1) > 0.004
        i, n = 0, prof.shape[0]
        while i < n and prof[i]:  # skip a remaining axis line
            i += 1
        while i < n and not prof[i]:
            i += 1
        s = i
        while i < n and prof[i]:
            i += 1
        band = i - s
        if 3 <= band <= 0.2 * H:
            font_px = band / CAP_HEIGHT_RATIO

    if curve_w is None and font_px is None:
        return None
    out: dict[str, Any] = {
        "measured_from": "crop",
        "crop_px": [int(W), int(H)],
        "plot_rect_used": [round(float(v), 4) for v in rect],
        "plot_rect_source": rect_source,
        "detected_rect": [round(float(v), 4) for v in detected] if detected else None,
        "stroke_source": stroke_source,
    }
    if curve_w is not None:
        out["stroke_px"] = round(curve_w, 2)
        out["stroke_frac"] = round(curve_w / H, 5)
        out["series_stroke_frac"] = [None if w is None else round(w / H, 5) for w in series_w]
    if grid_w is not None:
        out["grid_px"] = round(grid_w, 2)
        out["grid_frac"] = round(grid_w / H, 5)
    if font_px is not None:
        out["font_px"] = round(font_px, 2)
        out["font_frac"] = round(font_px / H, 5)
    return out
