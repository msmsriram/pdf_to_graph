import { useEffect } from "react";
import { isCategorical, PALETTE } from "./chartOption.js";

// Round to a sensible precision without destroying large/small magnitudes.
const tidy = (v) => {
  if (v == null || Number.isNaN(v)) return 0;
  return Number.parseFloat(Number(v).toPrecision(5));
};

/**
 * Adds direct mouse manipulation to a cartesian ECharts chart WITHOUT touching
 * the existing input-based editing:
 *   - drag any data point (handles overlaid via the `graphic` component)
 *   - double-click on the plot to add a new point to the active series
 *
 * When `enabled` is false (or the chart is a pie) it removes all handles and
 * the chart behaves exactly as before. Commits flow back through onCommitPoint /
 * onAddPoint, which update the same React `draft` (dirty -> Save version).
 */
export function useDraggablePoints({ chartRef, draft, theme, enabled, activeSeries, onCommitPoint, onAddPoint }) {
  useEffect(() => {
    const inst = chartRef.current?.getEchartsInstance?.();
    if (!inst) return;

    // NOTE: ECharts merges `graphic` by index, so `{graphic: []}` alone does NOT
    // remove existing handles. `replaceMerge: ['graphic']` forces a clean replace
    // (this is what makes the toggle disable handles immediately).
    const clearHandles = () => {
      try {
        inst.setOption({ graphic: [] }, { replaceMerge: ["graphic"] });
      } catch (_) {}
    };

    const isPie = draft.chart_type === "pie";
    if (!enabled || isPie) {
      clearHandles();
      return;
    }

    const categorical = isCategorical(draft);
    const colorOf = (si) => draft.series[si]?.color || PALETTE[si % PALETTE.length];

    // Live, mutable copy of series data in ECharts format for smooth drag feedback.
    const liveData = draft.series.map((se) =>
      (se.points || []).map((p) => (categorical ? p.y ?? 0 : [p.x ?? 0, p.y ?? 0]))
    );

    const dataValue = (si, pi) => {
      const p = draft.series[si].points[pi];
      return categorical ? [pi, p.y ?? 0] : [p.x ?? 0, p.y ?? 0];
    };

    const makeHandle = (si, pi) => {
      const pos = inst.convertToPixel("grid", dataValue(si, pi));
      if (!pos) return null;
      return {
        type: "circle",
        shape: { r: 7 },
        position: pos,
        style: { fill: colorOf(si), stroke: "#fff", lineWidth: 1.5, shadowBlur: 5, shadowColor: "rgba(0,0,0,0.35)" },
        draggable: true,
        cursor: "move",
        z: 300,
        ondrag: function () {
          const conv = inst.convertFromPixel("grid", this.position);
          if (!conv) return;
          if (categorical) {
            liveData[si][pi] = conv[1];
            // lock horizontal movement to the category slot
            const lockX = inst.convertToPixel("grid", [pi, conv[1]]);
            if (lockX) this.position = [lockX[0], this.position[1]];
          } else {
            liveData[si][pi] = conv;
          }
          inst.setOption({ series: liveData.map((d) => ({ data: d })) });
        },
        ondragend: function () {
          const conv = inst.convertFromPixel("grid", this.position);
          if (!conv) return;
          if (categorical) onCommitPoint(si, pi, { y: tidy(conv[1]) });
          else onCommitPoint(si, pi, { x: tidy(conv[0]), y: tidy(conv[1]) });
        },
      };
    };

    const build = () => {
      const graphic = [];
      draft.series.forEach((se, si) =>
        (se.points || []).forEach((_, pi) => {
          const h = makeHandle(si, pi);
          if (h) graphic.push(h);
        })
      );
      try {
        inst.setOption({ graphic }, { replaceMerge: ["graphic"] });
      } catch (_) {}
    };

    // Build after layout is ready (convertToPixel needs a laid-out grid).
    let raf = requestAnimationFrame(() => {
      build();
      // one more pass next frame in case the first was pre-layout
      raf = requestAnimationFrame(build);
    });

    // Double-click on the plot area to add a new point to the active series.
    const zr = inst.getZr();
    const onDbl = (e) => {
      const pt = [e.offsetX, e.offsetY];
      if (!inst.containPixel("grid", pt)) return;
      const conv = inst.convertFromPixel("grid", pt);
      if (!conv) return;
      const si = Math.min(activeSeries, draft.series.length - 1);
      if (categorical) onAddPoint(si, { label: "new", y: tidy(conv[1]) });
      else onAddPoint(si, { x: tidy(conv[0]), y: tidy(conv[1]) });
    };
    zr.on("dblclick", onDbl);

    // Reposition handles when the chart resizes.
    let ro;
    const dom = inst.getDom();
    if (dom && typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(() => requestAnimationFrame(build));
      ro.observe(dom);
    }

    return () => {
      cancelAnimationFrame(raf);
      zr.off("dblclick", onDbl);
      if (ro) ro.disconnect();
      clearHandles();
    };
  }, [enabled, activeSeries, theme, draft, chartRef, onCommitPoint, onAddPoint]);
}
