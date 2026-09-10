import React, { useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import ReactECharts from "echarts-for-react";
import { useStore } from "../store.js";
import { api } from "../lib/api.js";
import { specToOption } from "../lib/chartOption.js";

const STEPS = [
  { key: "uploaded", label: "Uploaded" },
  { key: "rendering", label: "Preparing pages (PDF render + images)" },
  { key: "analyzing", label: "Screening pages, extracting charts with the vision model" },
  { key: "complete", label: "Reconstructing & ready" },
];
const ORDER = ["uploaded", "rendering", "analyzing", "complete"];

function Stat({ num, label, cls }) {
  return (
    <div className="card stat">
      <div className={`num ${cls || ""}`}>{num}</div>
      <div className="lbl">{label}</div>
    </div>
  );
}

export default function Processing() {
  const { document, status, statusMessage, progress, logs, liveCharts, openWorkspace, documentId, theme } = useStore();
  const logRef = useRef(null);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logs]);

  const p = progress || {};
  const pagesTotal = p.pages_total || 0;
  const analyzed = p.pages_analyzed || 0;
  const pct = pagesTotal ? Math.round((analyzed / pagesTotal) * 100) : status === "complete" ? 100 : 0;
  const curIdx = ORDER.indexOf(status);

  const openResult = async () => {
    const doc = await api.getDocument(documentId);
    openWorkspace(doc);
  };

  return (
    <div className="container wide page">
      <div className="proc-head">
        <div>
          <h2 style={{ fontSize: 22, fontWeight: 650 }}>{document?.name}</h2>
          <div className="muted" style={{ marginTop: 4 }}>{statusMessage}</div>
        </div>
        {status === "complete" && (
          <button className="btn btn-primary" onClick={openResult}>
            Open workspace →
          </button>
        )}
        {status === "error" && <span className="pill error"><span className="dot" />Failed</span>}
      </div>

      {/* progress bar */}
      <div className="card" style={{ padding: 18, marginBottom: 22 }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 10, fontSize: 13 }}>
          <span className="muted">Overall progress</span>
          <span style={{ fontWeight: 600 }}>{pct}%</span>
        </div>
        <div className="progress-track">
          <div className="progress-fill" style={{ width: `${pct}%` }} />
        </div>
      </div>

      {/* stats */}
      <div className="stat-row">
        <Stat num={pagesTotal || "—"} label="Pages" />
        <Stat num={p.pages_rendered || 0} label="Rendered" cls="accent" />
        <Stat num={analyzed} label="Screened / read" cls="accent" />
        <Stat num={p.pages_skipped || 0} label="Skipped (no charts)" />
        <Stat num={p.charts_detected || 0} label="Charts found" cls="ok" />
        <Stat num={liveCharts.length} label="Reconstructed" cls="ok" />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, alignItems: "start" }}>
        {/* pipeline + logs */}
        <div className="card" style={{ padding: 18 }}>
          <div className="section-title" style={{ marginTop: 0 }}>Pipeline</div>
          <div className="pipeline">
            {STEPS.map((step) => {
              const idx = ORDER.indexOf(step.key);
              const done = curIdx > idx || status === "complete";
              const active = status === step.key && status !== "complete";
              return (
                <div key={step.key} className={`pipe-step ${done ? "done" : ""} ${active ? "active" : ""}`}>
                  <div className="pipe-check">{done ? "✓" : active ? <span className="pipe-spin" /> : ""}</div>
                  <span>{step.label}</span>
                </div>
              );
            })}
          </div>

          <div className="section-title">Live log</div>
          <div className="log-feed" ref={logRef}>
            {logs.length === 0 && <div className="log-line"><span className="lt">›</span> waiting for events…</div>}
            {logs.map((l, i) => (
              <div key={i} className={`log-line ${l.level}`}>
                <span className="lt">›</span>
                {l.message}
              </div>
            ))}
          </div>
        </div>

        {/* live chart appearance */}
        <div className="card" style={{ padding: 18 }}>
          <div className="section-title" style={{ marginTop: 0 }}>
            Charts appearing live ({liveCharts.length})
          </div>
          {liveCharts.length === 0 ? (
            <div className="empty-state" style={{ padding: "40px 10px" }}>
              <div className="icon">📊</div>
              Charts will pop in here as the model finds them.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 12, maxHeight: 460, overflowY: "auto" }}>
              <AnimatePresence>
                {liveCharts.map((c) => {
                  const spec = c.versions[c.current_version].spec;
                  return (
                    <motion.div
                      key={c.chart_id}
                      initial={{ opacity: 0, scale: 0.94, y: 10 }}
                      animate={{ opacity: 1, scale: 1, y: 0 }}
                      className="chart-canvas"
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", padding: "2px 6px 6px" }}>
                        <span style={{ fontSize: 12, fontWeight: 600 }}>{spec.title || c.chart_id}</span>
                        <span className="chip">page {c.page_number}</span>
                      </div>
                      <ReactECharts option={specToOption(spec, { theme, scale: 0.75 })} style={{ height: 200 }} notMerge lazyUpdate />
                    </motion.div>
                  );
                })}
              </AnimatePresence>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
