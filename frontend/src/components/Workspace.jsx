import React, { useEffect, useState } from "react";
import { useStore } from "../store.js";
import { api, assetUrl } from "../lib/api.js";
import ChartEditor from "./ChartEditor.jsx";

// Per-page status list: what the gate decided, what was extracted, and a
// "Process anyway" button for pages the gate skipped (runs the extraction model).
function PagesPanel({ document, compact }) {
  const setDocument = useStore((s) => s.setDocument);
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);
  const pages = document?.pages || [];
  if (!pages.length) return null;

  const force = async (n) => {
    setBusy(n);
    setErr(null);
    try {
      const doc = await api.analyzePage(document.document_id, n);
      setDocument(doc);
    } catch (e) {
      setErr(e?.response?.data?.detail || "Failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className={`pages-panel ${compact ? "compact" : ""}`}>
      <div className="rail-head" style={{ borderTop: "1px solid var(--border-soft)" }}>
        <span>Pages · {pages.length}</span>
      </div>
      <div className="pages-list">
        {pages.map((p) => {
          const g = p.gate;
          const skipped = p.status === "skipped";
          const n = (p.chart_ids || []).length;
          const state = skipped
            ? "skipped by gate"
            : p.status === "analyzed"
              ? `${n} chart${n === 1 ? "" : "s"}${g && g.overridden ? " · forced" : ""}`
              : p.status === "pending"
                ? "pending"
                : "";
          return (
            <div key={p.page_number} className={`page-row ${p.status || ""}`} title={g ? `${g.reason || ""}` : ""}>
              <span className="t">
                {p.kind === "image" ? "🖼 " : ""}
                {p.label || `Page ${p.page_number}`}
              </span>
              <span className="m">{state}</span>
              {skipped && (
                <button className="btn btn-sm" disabled={busy != null} onClick={() => force(p.page_number)} title="Run the extraction model on this page anyway">
                  {busy === p.page_number ? "Reading…" : "Process anyway"}
                </button>
              )}
            </div>
          );
        })}
        {err && <div className="warn-banner">⚠ {err}</div>}
      </div>
    </div>
  );
}

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
  const skippedCount = (document?.pages || []).filter((p) => p.status === "skipped").length;

  if (!charts.length) {
    return (
      <div className="container page">
        <div className="empty-state">
          <div className="icon">🔍</div>
          No charts were extracted from this document.
          {skippedCount > 0 && <div className="muted" style={{ marginTop: 8 }}>{skippedCount} page(s) were skipped by the gate — you can process any of them below.</div>}
        </div>
        <div className="card" style={{ marginTop: 16, maxWidth: 560 }}>
          <PagesPanel document={document} />
        </div>
      </div>
    );
  }

  return (
    <div className="ws-page page">
      <div className="ws-title">
        <h2>{document?.name}</h2>
        <div className="muted">
          {charts.length} charts across {document?.page_count} pages
          {skippedCount > 0 ? ` · ${skippedCount} skipped by the gate` : ""} · <kbd>[</kbd> / <kbd>]</kbd> toggle the side rails
        </div>
      </div>

      <div className="ws">
        {/* ---- left rail: chart list + page status ---- */}
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
              const page = (document?.pages || []).find((p) => p.page_number === c.page_number);
              const where = page && page.kind === "image" ? page.label : `page ${c.page_number}`;
              return (
                <div
                  key={c.chart_id}
                  className={`mini-chart ${c.chart_id === selected?.chart_id ? "selected" : ""}`}
                  onClick={() => selectChart(c.chart_id)}
                  title={`${title} · ${where}`}
                >
                  <img className="mini-thumb" src={assetUrl(docId, c.crop_image)} alt="" />
                  {leftRailOpen && (
                    <div className="mini-info">
                      <div className="t">{title}</div>
                      <div className="m">
                        {where} · {spec.chart_type}
                        {c.final_version != null ? " · ✓" : ""}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          {leftRailOpen && <PagesPanel document={document} compact />}
        </aside>

        {/* ---- stage + right rail (rendered by the editor into this grid) ---- */}
        {selected && <ChartEditor key={selected.chart_id} docId={docId} chart={selected} />}
      </div>
    </div>
  );
}
