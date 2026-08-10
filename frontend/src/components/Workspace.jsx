import React from "react";
import { useStore } from "../store.js";
import { assetUrl } from "../lib/api.js";
import ChartEditor from "./ChartEditor.jsx";

export default function Workspace() {
  const document = useStore((s) => s.document);
  const liveCharts = useStore((s) => s.liveCharts);
  const selectedChartId = useStore((s) => s.selectedChartId);
  const selectChart = useStore((s) => s.selectChart);

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
    <div className="container wide page">
      <div style={{ marginBottom: 18 }}>
        <h2 style={{ fontSize: 22, fontWeight: 650 }}>{document?.name}</h2>
        <div className="muted" style={{ marginTop: 4 }}>
          {charts.length} charts across {document?.page_count} pages
        </div>
      </div>

      <div className="workspace">
        {/* chart list */}
        <div className="chart-list">
          {charts.map((c) => {
            const spec = c.versions[c.current_version].spec;
            return (
              <div
                key={c.chart_id}
                className={`mini-chart ${c.chart_id === selected?.chart_id ? "selected" : ""}`}
                onClick={() => selectChart(c.chart_id)}
              >
                <img className="mini-thumb" src={assetUrl(docId, c.crop_image)} alt="" />
                <div className="mini-info">
                  <div className="t">{spec.title || c.chart_id}</div>
                  <div className="m">
                    page {c.page_number} · {spec.chart_type}
                    {c.final_version != null ? " · ✓" : ""}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {/* editor */}
        {selected && <ChartEditor key={selected.chart_id} docId={docId} chart={selected} />}
      </div>
    </div>
  );
}
