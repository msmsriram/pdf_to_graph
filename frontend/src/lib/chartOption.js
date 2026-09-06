// Convert a canonical ChartSpec into an ECharts option object.
// Supports: line, area, bar (grouped/stacked), scatter, pie.
//
// Options:
//   theme     "light" | "dark"
//   forExport clean white "print" style
//   showGrid  gridlines on/off
//   scale     size factor: 1 = a ~560px-wide chart. Every stroke, font, gap and
//             tick scales with it so a bigger chart looks like a ZOOMED original
//             (same proportions), not a stretched one with thin lines and tiny text.
//   overlay   curves-only rendering meant to be laid over the original crop image:
//             no text, no grid, transparent background, plot area pinned to
//             `plotRect` = [x0, y0, x1, y1] normalized within the crop.

export const PALETTE = ["#5b8cff", "#7c5cff", "#34d399", "#fbbf24", "#f87171", "#22d3ee", "#f472b6", "#a3e635"];
const FONT = "Arial, Helvetica, sans-serif";
// Curve stroke weights (px at scale 1) for the model-reported line_width; datasheet
// curves are typically ~2× the axis-line weight, so 'medium' is the default.
const LINE_WIDTH = { thin: 1.5, medium: 2.2, thick: 3.4 };

export function isCategorical(spec) {
  // Categorical when points carry labels and no numeric x (typical bar/pie).
  const s = spec.series || [];
  if (spec.chart_type === "pie") return true;
  const anyLabel = s.some((se) => (se.points || []).some((p) => p.label != null && p.label !== ""));
  const anyNumericX = s.some((se) => (se.points || []).some((p) => p.x != null));
  return anyLabel && !anyNumericX;
}

// "I_D (A)" -> "I{sub|D} (A)", "V_{DS}" -> "V{sub|DS}" : ECharts rich-text subscripts.
export function richify(text) {
  if (text == null || text === "") return "";
  return String(text)
    .replace(/([A-Za-z0-9)])_\{([^}]+)\}/g, "$1{sub|$2}")
    .replace(/([A-Za-z0-9)])_([A-Za-z0-9]+)/g, "$1{sub|$2}");
}

export function specToOption(
  spec,
  { theme = "dark", forExport = false, showGrid = true, scale = 1, overlay = false, plotRect = null, width = 0, height = 0 } = {}
) {
  const dark = theme === "dark";
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  // Sizing. When the crop has been measured (`style_metrics`: stroke / grid / font as
  // fractions of the crop height) and we know the rendered height, text and strokes are
  // sized to match the ORIGINAL at this size. Otherwise fall back to width-based scaling.
  const sm = spec.style_metrics || null;
  const measured = !!(sm && height > 0 && sm.font_frac > 0);
  const k = measured ? clamp((sm.font_frac * height) / 13, 0.5, 4) : clamp(Number(scale) || 1, 0.6, 2.2);
  const px = (v) => Math.round(v * k * 10) / 10;
  const strokePx = measured && sm.stroke_frac > 0 ? clamp(sm.stroke_frac * height, 1, 16) : LINE_WIDTH.medium * k;
  const gridW = measured && sm.grid_frac > 0 ? clamp(sm.grid_frac * height, 0.5, 4) : 1;

  // Plot-area geometry. Where the original's plot rectangle is known (the model's
  // plot_rect, else the frame the measurer detected) the grid is placed EXACTLY there —
  // the reconstruction then has the original's proportions and its text sits in the same
  // margins at the same measured size. Axis-title distances derive from those margins.
  const rectSrc = overlay ? plotRect : spec.plot_rect || (sm && sm.plot_rect_used) || null;
  const rect = Array.isArray(rectSrc) && rectSrc.length === 4 && width > 0 && height > 0 ? rectSrc : null;
  const marginL = rect ? rect[0] * width : 0;
  const marginT = rect ? rect[1] * height : 0;
  const marginR = rect ? (1 - rect[2]) * width : 0;
  const marginB = rect ? (1 - rect[3]) * height : 0;
  // ECharts places the axis title's CENTRE at nameGap from the axis line, so leave room
  // for half the font plus a margin: the title's outer edge ends ~px(15) from the pane edge.
  const yNameGap = rect ? Math.max(px(24), marginL - px(22)) : px(42);
  const xNameGap = rect ? Math.max(px(18), marginB - px(22)) : px(30);

  const axisColor = forExport ? "#444" : dark ? "#8290b0" : "#555";
  // Major gridlines are clearly visible (datasheet-like); minor ones are lighter.
  const gridColor = forExport ? "rgba(0,0,0,0.30)" : dark ? "rgba(255,255,255,0.18)" : "rgba(0,0,0,0.22)";
  const minorGridColor = forExport ? "rgba(0,0,0,0.13)" : dark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.10)";
  const textColor = forExport ? "#222" : dark ? "#aeb9d4" : "#333";
  const titleColor = forExport ? "#111" : dark ? "#eef2fb" : "#111";
  const series = spec.series || [];
  const displayName = (se) => (se.conditions ? `${se.name} · ${se.conditions}` : se.name);
  // Subscript fragment style used wherever text may contain X_Y.
  const richFor = (color, base) => ({
    sub: { color, fontFamily: FONT, fontSize: px(base * 0.72), verticalAlign: "bottom", padding: [px(base * 0.45), 0, 0, 0] },
  });

  const base = {
    backgroundColor: overlay ? "transparent" : forExport ? "#ffffff" : "transparent",
    textStyle: { color: textColor, fontFamily: FONT },
    title: {
      text: overlay ? "" : richify(spec.title || ""),
      subtext: overlay ? "" : richify(spec.subtitle || ""),
      left: "center",
      textStyle: { color: titleColor, fontSize: px(15), fontWeight: 500, fontFamily: FONT, rich: richFor(titleColor, 15) },
      subtextStyle: { color: axisColor, fontSize: px(11), fontFamily: FONT, rich: richFor(axisColor, 11) },
    },
    tooltip: overlay ? { show: false } : { trigger: spec.chart_type === "pie" ? "item" : "axis", confine: true },
    // Legend lists only the real series (helper series like the log-paper grid are excluded).
    legend:
      spec.legend && !overlay
        ? { bottom: 0, data: series.map(displayName), textStyle: { color: textColor, fontSize: px(11), fontFamily: FONT }, type: "scroll" }
        : undefined,
    // show + border draws the full rectangular plot frame datasheets use.
    grid: {
      left: px(55), right: px(24), top: spec.title ? px(56) : px(28), bottom: spec.legend ? px(42) : px(30),
      containLabel: true, show: true, backgroundColor: "transparent", borderColor: axisColor, borderWidth: gridW,
    },
    animationDuration: 500,
  };

  // Pin the plot area onto the original's axes rectangle (overlay: always; normal view:
  // whenever the rectangle is known).
  if (overlay || rect) {
    const r = rect || [0.12, 0.08, 0.96, 0.9];
    base.grid = {
      left: `${(r[0] * 100).toFixed(2)}%`,
      top: `${(r[1] * 100).toFixed(2)}%`,
      width: `${((r[2] - r[0]) * 100).toFixed(2)}%`,
      height: `${((r[3] - r[1]) * 100).toFixed(2)}%`,
      containLabel: false,
      show: !overlay,
      backgroundColor: "transparent",
      borderColor: axisColor,
      borderWidth: gridW,
    };
    if (overlay) base.animationDuration = 0;
    // title vertically centred in the top margin
    if (!overlay && spec.title) base.title.top = Math.max(0, marginT / 2 - px(10));
  }

  // In-plot note boxes (rendered as extra `title` elements — NOT the `graphic`
  // component, which is reserved for the drag handles, so the two never collide).
  // Anchored just inside the plot-area corner; several notes sharing a corner stack.
  const stack = { "top-left": 0, "top-right": 0, "bottom-left": 0, "bottom-right": 0 };
  const annTitles = overlay
    ? []
    : (spec.annotations || [])
        .filter((a) => a && a.text)
        .map((a) => {
          const pos = a.position || "top-left";
          const key = `${pos.indexOf("top") >= 0 ? "top" : "bottom"}-${pos.indexOf("left") >= 0 ? "left" : "right"}`;
          const lines = String(a.text).split("\n").length;
          const blockH = lines * px(16) + px(10);
          const box = {};
          if (pos.indexOf("left") >= 0) box.left = (rect ? marginL : px(70)) + px(5);
          else box.right = (rect ? marginR : px(40)) + px(5);
          if (pos.indexOf("top") >= 0) box.top = (rect ? marginT : px(62)) + px(3) + stack[key];
          else box.bottom = (rect ? marginB : px(56)) + px(3) + stack[key];
          stack[key] += blockH;
          return {
            text: richify(a.text),
            ...box,
            textAlign: "left",
            textStyle: { color: textColor, fontSize: px(12), fontWeight: 400, lineHeight: px(16), fontFamily: FONT, rich: richFor(textColor, 12) },
            backgroundColor: forExport ? "rgba(255,255,255,0.92)" : "transparent",
            padding: [px(3), px(5)],
          };
        });
  base.title = [base.title, ...annTitles];

  // ---- PIE ----
  if (spec.chart_type === "pie") {
    const src = series[0]?.points || [];
    return {
      ...base,
      grid: undefined,
      xAxis: undefined,
      yAxis: undefined,
      series: [
        {
          type: "pie",
          radius: ["38%", "66%"],
          center: ["50%", "54%"],
          data: src.map((p, i) => ({
            name: p.label || `Item ${i + 1}`,
            value: p.y ?? 0,
            itemStyle: { color: PALETTE[i % PALETTE.length] },
          })),
          label: { color: textColor, fontSize: px(12), fontFamily: FONT },
        },
      ],
    };
  }

  const categorical = isCategorical(spec);
  const axisName = (ax) => richify([ax?.label, ax?.unit ? `(${ax.unit})` : ""].filter(Boolean).join(" "));
  const hiddenAxisBits = overlay
    ? { axisLabel: { show: false }, axisLine: { show: false }, axisTick: { show: false }, name: "", splitLine: { show: false }, minorSplitLine: { show: false }, minorTick: { show: false } }
    : {};

  let xAxis, yAxis;
  if (categorical) {
    const cats = (series[0]?.points || []).map((p, i) => p.label ?? String(i));
    xAxis = {
      type: "category",
      data: cats,
      name: axisName(spec.x_axis),
      nameLocation: "middle",
      nameGap: xNameGap,
      axisLine: { lineStyle: { color: axisColor, width: gridW } },
      axisLabel: { color: axisColor, fontSize: px(13), fontFamily: FONT },
      splitLine: { show: showGrid, lineStyle: { color: gridColor, width: gridW } },
      nameTextStyle: { color: textColor, fontSize: px(13), fontFamily: FONT, rich: richFor(textColor, 13) },
      ...hiddenAxisBits,
    };
  } else {
    xAxis = {
      type: spec.x_axis?.scale === "log" ? "log" : "value",
      name: axisName(spec.x_axis),
      nameLocation: "middle",
      nameGap: xNameGap,
      min: spec.x_axis?.min ?? undefined,
      max: spec.x_axis?.max ?? undefined,
      inverse: spec.x_axis?.inverse === true, // values decrease to the right
      axisLine: { lineStyle: { color: axisColor, width: gridW } },
      axisLabel: { color: axisColor, fontSize: px(13), fontFamily: FONT },
      splitLine: { show: showGrid, lineStyle: { color: gridColor, width: gridW } },
      nameTextStyle: { color: textColor, fontSize: px(13), fontFamily: FONT, rich: richFor(textColor, 13) },
      ...linearTicks(spec.x_axis, showGrid, minorGridColor),
      ...hiddenAxisBits,
    };
  }
  yAxis = {
    type: spec.y_axis?.scale === "log" ? "log" : "value",
    name: axisName(spec.y_axis),
    nameLocation: "middle",
    nameGap: yNameGap,
    min: spec.y_axis?.min ?? undefined,
    max: spec.y_axis?.max ?? undefined,
    inverse: spec.y_axis?.inverse === true, // values decrease going up (e.g. 0 at bottom, -1 at top)
    axisLine: { lineStyle: { color: axisColor, width: gridW } },
    axisLabel: { color: axisColor, fontSize: px(13), fontFamily: FONT },
    splitLine: { show: showGrid, lineStyle: { color: gridColor, width: gridW } },
    nameTextStyle: { color: textColor, fontSize: px(13), fontFamily: FONT, rich: richFor(textColor, 13) },
    ...linearTicks(spec.y_axis, showGrid, minorGridColor),
    ...hiddenAxisBits,
  };

  // Inline end-labels ("6V", "5V", ...) placed on the plot like a datasheet.
  const inlineLabels =
    !overlay && spec.inline_labels !== false && !categorical && (spec.chart_type === "line" || spec.chart_type === "area");

  const echSeries = series.map((se, i) => {
    const color = se.color || PALETTE[i % PALETTE.length];
    const t = spec.chart_type === "scatter" ? "scatter" : spec.chart_type === "bar" ? "bar" : "line";
    const raw = categorical
      ? (se.points || []).map((p) => p.y ?? 0)
      : (se.points || []).map((p) => [p.x ?? 0, p.y ?? 0]);
    // Smooth curves by densifying the DATA with a shape-preserving monotone cubic
    // (PCHIP): it rounds the corners between samples and is guaranteed not to
    // overshoot or wobble — unlike a bezier `smooth`, which wavers between unevenly
    // spaced samples. Off for straight-segment plots (spec.smooth=false) and when
    // markers are shown (markers must sit on the real samples).
    const densify = t === "line" && !categorical && spec.smooth !== false && spec.show_markers !== true;
    const data = densify
      ? pchipDensify(raw, { logX: spec.x_axis?.scale === "log", logY: spec.y_axis?.scale === "log" })
      : raw;
    // A label anchor means the original prints this curve's name at a specific spot on
    // the plot; we place it there (below) instead of at the line's end.
    const hasAnchor = !categorical && t === "line" && Number.isFinite(se.label_x) && Number.isFinite(se.label_y);
    // Stroke weight: the crop's measured curve stroke (matches the original at this size),
    // with the model's per-series thin/medium/thick as a relative modifier (bold limit
    // lines stay bold). Without measurements, fixed widths scaled by k.
    const mult = { thin: 0.7, medium: 1, thick: 1.4 }[se.line_width] ?? 1;
    // Per-series measured stroke (measured along that very curve) wins; otherwise the
    // chart's overall stroke × the model's modifier; otherwise fixed widths scaled by k.
    let own = measured && Array.isArray(sm.series_stroke_frac) && sm.series_stroke_frac[i] > 0 ? sm.series_stroke_frac[i] * height : null;
    // Two curves drawn on top of each other measure as one double-width stroke; unless the
    // model says this curve really is bold, cap it at 1.6× the chart's typical stroke.
    if (own && se.line_width !== "thick" && own > 1.6 * strokePx) own = strokePx;
    const lineW = own
      ? Math.round(clamp(own, 1, 16) * 10) / 10
      : measured
        ? Math.round(strokePx * mult * 10) / 10
        : px(LINE_WIDTH[se.line_width] ?? LINE_WIDTH.medium);
    const s = {
      name: displayName(se),
      type: t,
      data,
      itemStyle: { color },
      lineStyle: { color, width: lineW },
      symbolSize: t === "scatter" ? px(8) : px(5),
      smooth: false,
    };
    // Markers: only when the original actually had point markers (default off,
    // matching clean datasheet lines). Scatter always shows its points.
    if (t === "line") s.showSymbol = spec.show_markers === true;
    if (inlineLabels && t === "line" && !hasAnchor) {
      s.endLabel = {
        show: true,
        formatter: () => richify(se.name || ""),
        color: textColor,
        fontSize: px(12),
        fontWeight: 600,
        fontFamily: FONT,
        distance: px(5),
        rich: richFor(textColor, 12),
      };
      s.labelLayout = { moveOverlap: "shiftY" };
    }
    if (spec.chart_type === "area") s.areaStyle = { color, opacity: overlay ? 0.1 : 0.18 };
    if (spec.chart_type === "bar" && spec.stacked) s.stack = "total";
    return s;
  });

  // ---- On-plot curve labels at their original anchor positions ----
  // One silent, legend-less scatter point per anchored series carrying the label,
  // centred on the anchor (optionally boxed like datasheet callouts). Appended AFTER
  // the real series so index-based data merges (drag) stay aligned.
  if (!categorical && !overlay) {
    // Series whose anchors coincide are a bundle sharing ONE printed label (e.g.
    // 'VGS=7~10V (upper, cyan)' + 'VGS=7~10V (lower, pink)'), so draw a single label
    // with the shared name instead of stacking two on the same spot.
    const groups = new Map();
    series.forEach((se, i) => {
      if (!(Number.isFinite(se.label_x) && Number.isFinite(se.label_y))) return;
      const key = `${Number(se.label_x).toPrecision(4)}|${Number(se.label_y).toPrecision(4)}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push({ se, i });
    });
    groups.forEach((members) => {
      const { se, i } = members[0];
      const boxed = members.some((m) => m.se.label_boxed === true);
      const text = richify(members.length === 1 ? se.name || "" : sharedLabel(members.map((m) => m.se.name || "")));
      const boxBg = forExport || !dark ? "#ffffff" : "#0f1524";
      echSeries.push({
        name: `__label_${i}__`,
        type: "scatter",
        data: [[se.label_x, se.label_y]],
        symbolSize: 1,
        silent: true,
        legendHoverLink: false,
        tooltip: { show: false },
        z: 6,
        itemStyle: { color: "transparent" },
        label: {
          show: true,
          position: "inside",
          formatter: () => text,
          color: textColor,
          fontSize: px(12),
          fontWeight: 600,
          fontFamily: FONT,
          backgroundColor: boxed ? boxBg : "transparent",
          borderColor: boxed ? textColor : "transparent",
          borderWidth: boxed ? 1 : 0,
          padding: boxed ? [px(3), px(6)] : 0,
          rich: richFor(textColor, 12),
        },
      });
    });
  }

  // Give the end-labels room so they aren't clipped at the right edge.
  if (inlineLabels) base.grid.right = px(82);

  // ---- Log-paper minor gridlines (2,3,…,9 in every decade) ----
  // Real datasheet log axes show these lines; ECharts' built-in log minor ticks are
  // evenly spaced in log-space (1.29, 1.67, …) which is NOT the classic pattern, so we
  // draw the exact 2–9 lines ourselves via a silent markLine helper series. It is
  // appended AFTER the real series so index-based data merges (drag) stay aligned.
  const markData = [];
  if (showGrid && !overlay) {
    if (!categorical && spec.x_axis?.scale === "log") {
      logPaperLines(axisExtent(spec.x_axis, series, (p) => p.x)).forEach((v) => markData.push({ xAxis: v }));
    }
    if (spec.y_axis?.scale === "log") {
      logPaperLines(axisExtent(spec.y_axis, series, (p) => p.y)).forEach((v) => markData.push({ yAxis: v }));
    }
  }
  if (markData.length) {
    echSeries.push({
      name: "__grid__",
      type: "line",
      data: [],
      silent: true,
      showSymbol: false,
      legendHoverLink: false,
      tooltip: { show: false },
      markLine: {
        silent: true,
        symbol: ["none", "none"],
        label: { show: false },
        animation: false,
        z: 1, // under the curves (series z=2), above the plot background
        lineStyle: { color: minorGridColor, width: 1, type: "solid" },
        data: markData,
      },
    });
  }

  return { ...base, xAxis, yAxis, series: echSeries };
}

/* ------------------------------------------------------------------ helpers */
// The name a bundle of series shares: their longest common prefix, minus a dangling
// separator (e.g. 'VGS=7~10V (upper)' + 'VGS=7~10V (lower)' -> 'VGS=7~10V').
function sharedLabel(names) {
  let p = names[0] || "";
  for (const n of names.slice(1)) {
    let k = 0;
    while (k < p.length && k < n.length && p[k] === n[k]) k++;
    p = p.slice(0, k);
  }
  p = p.replace(/[\s(\-–·,:/]+$/, "").trim();
  return p || names.join(" / ");
}

// Shape-preserving densification (PCHIP, Fritsch–Carlson slopes). Given sparse
// [x,y] samples it returns ~`per` interpolated points per segment that pass
// exactly through the samples, never overshoot, and never oscillate. On log axes
// the interpolation runs in log10 space so the curve looks right on log paper.
export function pchipDensify(points, { logX = false, logY = false, per = 12 } = {}) {
  // clean: finite, sorted by x, unique x (last wins)
  const clean = [];
  (points || []).forEach((p) => {
    const x = Number(p[0]), y = Number(p[1]);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    if (clean.length && clean[clean.length - 1][0] === x) clean[clean.length - 1] = [x, y];
    else clean.push([x, y]);
  });
  clean.sort((a, b) => a[0] - b[0]);
  const n = clean.length;
  if (n < 3) return clean;
  // log space only if every value is positive
  const useLogX = logX && clean.every((p) => p[0] > 0);
  const useLogY = logY && clean.every((p) => p[1] > 0);
  const fx = useLogX ? Math.log10 : (v) => v;
  const fy = useLogY ? Math.log10 : (v) => v;
  const gx = useLogX ? (v) => Math.pow(10, v) : (v) => v;
  const gy = useLogY ? (v) => Math.pow(10, v) : (v) => v;
  const xs = clean.map((p) => fx(p[0]));
  const ys = clean.map((p) => fy(p[1]));

  const h = [], d = [];
  for (let i = 0; i < n - 1; i++) {
    h[i] = xs[i + 1] - xs[i];
    d[i] = h[i] !== 0 ? (ys[i + 1] - ys[i]) / h[i] : 0;
  }
  const m = new Array(n).fill(0);
  // interior slopes (Fritsch–Carlson): zero at local extrema, weighted harmonic mean otherwise
  for (let i = 1; i < n - 1; i++) {
    if (d[i - 1] * d[i] <= 0) m[i] = 0;
    else {
      const w1 = 2 * h[i] + h[i - 1], w2 = h[i] + 2 * h[i - 1];
      m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i]);
    }
  }
  // end slopes: one-sided three-point formula, clamped to keep the shape
  const endSlope = (h0, h1, d0, d1) => {
    let s = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1);
    if (Math.sign(s) !== Math.sign(d0)) s = 0;
    else if (Math.sign(d0) !== Math.sign(d1) && Math.abs(s) > Math.abs(3 * d0)) s = 3 * d0;
    return s;
  };
  m[0] = endSlope(h[0], h[1], d[0], d[1]);
  m[n - 1] = endSlope(h[n - 2], h[n - 3], d[n - 2], d[n - 3]);

  const out = [];
  for (let i = 0; i < n - 1; i++) {
    const x0 = xs[i], y0 = ys[i], y1 = ys[i + 1], hi = h[i];
    for (let t0 = 0; t0 < per; t0++) {
      const t = t0 / per, t2 = t * t, t3 = t2 * t;
      const h00 = 2 * t3 - 3 * t2 + 1, h10 = t3 - 2 * t2 + t, h01 = -2 * t3 + 3 * t2, h11 = t3 - t2;
      const y = h00 * y0 + h10 * hi * m[i] + h01 * y1 + h11 * hi * m[i + 1];
      out.push([gx(x0 + t * hi), gy(y)]);
    }
  }
  out.push([gx(xs[n - 1]), gy(ys[n - 1])]);
  return out;
}

// Pin a LINEAR axis to the tick spacing read off the original (major step and, if the
// plot has finer gridlines, the minor step). Log axes are handled by logPaperLines.
function linearTicks(axis, showGrid, minorColor) {
  if (!axis || axis.scale === "log") return {};
  const out = {};
  const major = axis.major_interval;
  const minor = axis.minor_interval;
  if (major > 0) out.interval = major;
  if (major > 0 && minor > 0 && minor < major) {
    const n = Math.max(2, Math.round(major / minor));
    out.minorTick = { show: true, splitNumber: n };
    out.minorSplitLine = { show: showGrid, lineStyle: { color: minorColor } };
  }
  return out;
}

// [min, max] for a log axis: use the spec's min/max when given, else derive from
// the data and expand to whole decades (which is how ECharts auto-ranges log axes).
function axisExtent(axis, series, getter) {
  let lo = axis?.min, hi = axis?.max;
  if (!(lo > 0) || !(hi > lo)) {
    let dlo = Infinity, dhi = -Infinity;
    series.forEach((se) =>
      (se.points || []).forEach((p) => {
        const v = getter(p);
        if (v != null && Number.isFinite(v) && v > 0) {
          dlo = Math.min(dlo, v);
          dhi = Math.max(dhi, v);
        }
      })
    );
    if (!Number.isFinite(dlo)) return null;
    lo = Math.pow(10, Math.floor(Math.log10(dlo)));
    hi = Math.pow(10, Math.ceil(Math.log10(dhi)));
    if (hi <= lo) hi = lo * 10;
  }
  return [lo, hi];
}

// Values 2·10^k … 9·10^k strictly inside (min, max) — the classic log-paper lines.
function logPaperLines(extent) {
  if (!extent) return [];
  const [min, max] = extent;
  const out = [];
  const kMin = Math.floor(Math.log10(min));
  const kMax = Math.ceil(Math.log10(max));
  for (let k = kMin; k <= kMax; k++) {
    for (let m = 2; m <= 9; m++) {
      const v = m * Math.pow(10, k);
      if (v > min && v < max) out.push(v);
    }
  }
  return out;
}
