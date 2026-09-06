import React, { useEffect } from "react";
import { useStore } from "../store.js";
import { assetUrl } from "../lib/api.js";
import ChartEditor from "./ChartEditor.jsx";

// Workspace layout: [chart-list rail] [stage: original | reconstructed] [editor rail]
// Both rails collapse (chevrons, or `[` / `]`), and the stage takes the room.
export default function Workspace() {
  const document = useStore((s) => s.document);
  const liveCharts = useStore((s) => s.liveCharts);
  const selectedChartId = useStore((s) => s.selectedChartId);
  const selectChart = useStore((s) => s.selectChart);
  const leftRailOpen = useStore((s) => s.leftRailOpen);
  const toggleRail = useStore((s) => s.toggleRail);

  // Keyboard: [ toggles the chart list, ] toggles the editor (ignored while typing).
  useEffect(() => {
    const onKey = (e) => {
      const t = e.target;
      const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
      if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "[") toggleRail("left");
      else if (e.key === "]") toggleRail("right");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggleRail]);

  const charts = (document?.charts && document.charts.length ? document.charts : liveCharts) || [];
  const selected = charts.find((c) => c.chart_id === selectedChartId) || charts[0];
  const docId = document?.document_id;

  if (!charts.length) {
    return (
      <div className="container page">
        <div className="empty-state">
          <div className="icon">🔍</div>
          No charts were detected in this PDF.
        </div>
      </div>
    );
  }

  return (
    <div className="ws-page page">
      <div className="ws-title">
        <h2>{document?.name}</h2>
        <div className="muted">
          {charts.length} charts across {document?.page_count} pages · <kbd>[</kbd> / <kbd>]</kbd> toggle the side rails
        </div>
      </div>

      <div className="ws">
        {/* ---- left rail: chart list ---- */}
        <aside className={`rail rail-left ${leftRailOpen ? "" : "collapsed"}`}>
          <div className="rail-head">
            {leftRailOpen && <span>Charts · {charts.length}</span>}
            <button className="rail-toggle" onClick={() => toggleRail("left")} title={leftRailOpen ? "Collapse chart list  [" : "Expand chart list  ["}>
              {leftRailOpen ? "◂" : "▸"}
            </button>
          </div>
          <div className="rail-list">
            {charts.map((c) => {
              const spec = c.versions[c.current_version].spec;
              const title = spec.title || c.chart_id;
              return (
                <div
                  key={c.chart_id}
                  className={`mini-chart ${c.chart_id === selected?.chart_id ? "selected" : ""}`}
                  onClick={() => selectChart(c.chart_id)}
                  title={`${title} · page ${c.page_number}`}
                >
                  <img className="mini-thumb" src={assetUrl(docId, c.crop_image)} alt="" />
                  {leftRailOpen && (
                    <div className="mini-info">
                      <div className="t">{title}</div>
                      <div className="m">
                        page {c.page_number} · {spec.chart_type}
                        {c.final_version != null ? " · ✓" : ""}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </aside>

        {/* ---- stage + right rail (rendered by the editor into this grid) ---- */}
        {selected && <ChartEditor key={selected.chart_id} docId={docId} chart={selected} />}
      </div>
    </div>
  );
}
