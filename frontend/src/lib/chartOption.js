// Convert a canonical ChartSpec into an ECharts option object.
// Supports: line, area, bar (grouped/stacked), scatter, pie.

export const PALETTE = ["#5b8cff", "#7c5cff", "#34d399", "#fbbf24", "#f87171", "#22d3ee", "#f472b6", "#a3e635"];

export function isCategorical(spec) {
  // Categorical when points carry labels and no numeric x (typical bar/pie).
  const s = spec.series || [];
  if (spec.chart_type === "pie") return true;
  const anyLabel = s.some((se) => (se.points || []).some((p) => p.label != null && p.label !== ""));
  const anyNumericX = s.some((se) => (se.points || []).some((p) => p.x != null));
  return anyLabel && !anyNumericX;
}

export function specToOption(spec, { theme = "dark", forExport = false, showGrid = true } = {}) {
  const dark = theme === "dark";
  // forExport => clean, PDF-like style (white background, clearly visible gridlines)
  // so PNG/JPEG/SVG carry the same gridlines as the original chart.
  const axisColor = forExport ? "#555" : dark ? "#6b789a" : "#666";
  const gridColor = forExport ? "rgba(0,0,0,0.18)" : dark ? "rgba(255,255,255,0.07)" : "rgba(0,0,0,0.09)";
  const textColor = forExport ? "#222" : dark ? "#aeb9d4" : "#333";
  const titleColor = forExport ? "#111" : dark ? "#eef2fb" : "#111";
  const series = spec.series || [];

  const base = {
    backgroundColor: forExport ? "#ffffff" : "transparent",
    textStyle: { color: textColor, fontFamily: "inherit" },
    title: {
      text: spec.title || "",
      subtext: spec.subtitle || "",
      left: "center",
      textStyle: { color: titleColor, fontSize: 15, fontWeight: 600 },
      subtextStyle: { color: axisColor, fontSize: 11 },
    },
    tooltip: { trigger: spec.chart_type === "pie" ? "item" : "axis", confine: true },
    legend: spec.legend
      ? { bottom: 0, textStyle: { color: textColor, fontSize: 11 }, type: "scroll" }
      : undefined,
    grid: { left: 55, right: 24, top: spec.title ? 56 : 28, bottom: spec.legend ? 42 : 30, containLabel: true },
    animationDuration: 500,
  };

  // In-plot note boxes (rendered as extra `title` elements — NOT the `graphic`
  // component, which is reserved for the drag handles, so the two never collide).
  const annTitles = (spec.annotations || [])
    .filter((a) => a && a.text)
    .map((a) => {
      const pos = a.position || "top-left";
      const box = {};
      if (pos.indexOf("left") >= 0) box.left = 70;
      else box.right = 40;
      if (pos.indexOf("top") >= 0) box.top = 62;
      else box.bottom = 56;
      return {
        text: a.text,
        ...box,
        textAlign: "left",
        textStyle: { color: textColor, fontSize: 11, fontWeight: 400, lineHeight: 15 },
        backgroundColor: forExport ? "rgba(255,255,255,0.92)" : "transparent",
        padding: [3, 6],
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
          label: { color: textColor },
        },
      ],
    };
  }

  const categorical = isCategorical(spec);
  const axisName = (ax) => [ax?.label, ax?.unit ? `(${ax.unit})` : ""].filter(Boolean).join(" ");

  let xAxis, yAxis;
  if (categorical) {
    const cats = (series[0]?.points || []).map((p, i) => p.label ?? String(i));
    xAxis = {
      type: "category",
      data: cats,
      name: axisName(spec.x_axis),
      nameLocation: "middle",
      nameGap: 30,
      axisLine: { lineStyle: { color: axisColor } },
      axisLabel: { color: axisColor },
      splitLine: { show: showGrid, lineStyle: { color: gridColor } },
      nameTextStyle: { color: textColor },
    };
  } else {
    xAxis = {
      type: spec.x_axis?.scale === "log" ? "log" : "value",
      name: axisName(spec.x_axis),
      nameLocation: "middle",
      nameGap: 30,
      min: spec.x_axis?.min ?? undefined,
      max: spec.x_axis?.max ?? undefined,
      axisLine: { lineStyle: { color: axisColor } },
      axisLabel: { color: axisColor },
      splitLine: { show: showGrid, lineStyle: { color: gridColor } },
      nameTextStyle: { color: textColor },
    };
  }
  yAxis = {
    type: spec.y_axis?.scale === "log" ? "log" : "value",
    name: axisName(spec.y_axis),
    nameLocation: "middle",
    nameGap: 42,
    min: spec.y_axis?.min ?? undefined,
    max: spec.y_axis?.max ?? undefined,
    axisLine: { lineStyle: { color: axisColor } },
    axisLabel: { color: axisColor },
    splitLine: { show: showGrid, lineStyle: { color: gridColor } },
    nameTextStyle: { color: textColor },
  };

  // Inline end-labels ("6V", "5V", ...) placed on the plot like a datasheet.
  const inlineLabels =
    spec.inline_labels !== false && !categorical && (spec.chart_type === "line" || spec.chart_type === "area");

  const echSeries = series.map((se, i) => {
    const color = se.color || PALETTE[i % PALETTE.length];
    const t = spec.chart_type === "scatter" ? "scatter" : spec.chart_type === "bar" ? "bar" : "line";
    const data = categorical
      ? (se.points || []).map((p) => p.y ?? 0)
      : (se.points || []).map((p) => [p.x ?? 0, p.y ?? 0]);
    const s = {
      name: se.conditions ? `${se.name} · ${se.conditions}` : se.name,
      type: t,
      data,
      itemStyle: { color },
      lineStyle: { color, width: 2 },
      symbolSize: t === "scatter" ? 8 : 5,
      smooth: false,
    };
    // Markers: only when the original actually had point markers (default off,
    // matching clean datasheet lines). Scatter always shows its points.
    if (t === "line") s.showSymbol = spec.show_markers === true;
    if (inlineLabels && t === "line") {
      s.endLabel = { show: true, formatter: () => se.name || "", color: textColor, fontSize: 11, fontWeight: 600, distance: 5 };
      s.labelLayout = { moveOverlap: "shiftY" };
    }
    if (spec.chart_type === "area") s.areaStyle = { color, opacity: 0.18 };
    if (spec.chart_type === "bar" && spec.stacked) s.stack = "total";
    return s;
  });

  // Give the end-labels room so they aren't clipped at the right edge.
  if (inlineLabels) base.grid.right = 82;

  return { ...base, xAxis, yAxis, series: echSeries };
}
