import React, { useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import ReactECharts from "echarts-for-react";
import { useStore } from "../store.js";
import { api, assetUrl } from "../lib/api.js";
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

const PHASE_COPY = {
  ready: ["Page prepared", "Waiting for its turn in the extraction sequence."],
  gate: ["Gate is scanning", "Separating data charts from tables, diagrams and text."],
  passed: ["Chart signal confirmed", "This page is moving into Astra's vision pipeline."],
  skipped: ["No data chart detected", "The gate saved a full extraction call for this page."],
  astra: ["Astra is examining the page", "Reading geometry, scales, labels and curve relationships."],
  streaming: ["Structured output is arriving", "Completed fields are becoming a provisional reconstruction."],
  constructing: ["Building the chart", "Axes, series and data points are settling into place."],
  validated: ["Validated and saved", "The final structured result is now authoritative."],
  error: ["Page needs attention", "This page failed safely; the remaining pages will continue."],
  cancelled: ["Processing stopped", "No further model output will be saved for this job."],
};

const phaseLabel = (phase) => PHASE_COPY[phase]?.[0] || "Preparing the visual workspace";

function cleanReasoning(text) {
  return (text || "")
    .replace(/\*\*/g, "")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^[*-]\s+/gm, "")
    .trim();
}

function CognitiveCloud({ text, fallback, phase }) {
  const scrollRef = useRef(null);
  const clean = cleanReasoning(text);
  const paragraphs = (clean || fallback || "").split(/\n\s*\n|\n/).filter(Boolean);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [text]);

  return (
    <section className={`cognitive-cloud phase-${phase} ${clean ? "receiving" : "idle"}`} aria-label="Astra activity summary">
      <div className="cloud-fog" aria-hidden="true"><i /><i /><i /><i /><i /></div>
      <span className="reasoning-mark">A</span>
      <div className="cloud-content">
        <strong>{clean ? "Astra summary" : "Live activity"}</strong>
        <div className="cloud-copy" ref={scrollRef} aria-live="polite" aria-atomic="false">
          {paragraphs.map((paragraph, index) => <p className={index === paragraphs.length - 1 ? "latest" : ""} key={`${index}-${paragraph.slice(0, 24)}`}>{paragraph}</p>)}
        </div>
      </div>
      {clean && <span className="cloud-caret" aria-hidden="true" />}
    </section>
  );
}

function draftSpec(draft) {
  return {
    title: draft.title || "Chart emerging…",
    subtitle: draft.subtitle || null,
    title_position: draft.title_position || "above",
    chart_type: draft.chart_type || "line",
    stacked: draft.stacked || false,
    x_axis: draft.x_axis || {},
    y_axis: draft.y_axis || {},
    series: (draft.series || []).filter(Boolean),
    legend: draft.legend ?? true,
    inline_labels: draft.inline_labels ?? false,
    show_markers: draft.status !== "validated",
    smooth: draft.smooth ?? false,
    annotations: draft.annotations || [],
    plot_rect: draft.plot_rect || null,
    confidence: draft.confidence || {},
  };
}

function DocumentStopButton() {
  const documentId = useStore((s) => s.documentId);
  const job = useStore((s) => s.documentJob);
  const [requesting, setRequesting] = React.useState(false);
  const active = job && ["running", "cancelling"].includes(job.status);

  const stop = async () => {
    if (!active || requesting) return;
    setRequesting(true);
    try {
      await api.cancelJob(documentId, job.job_id);
    } catch (_) {
      // A completion can win the same instant as Stop; the terminal event remains authoritative.
    } finally {
      setRequesting(false);
    }
  };

  return (
    <button className="btn btn-sm btn-stop" onClick={stop} disabled={!active || requesting || job?.status === "cancelling"} title={active ? "Stop the active document job" : "No document job is currently running"}>
      <span aria-hidden="true">■</span> {requesting || job?.status === "cancelling" ? "Stopping…" : "Stop"}
    </button>
  );
}

export default function Processing() {
  const processingView = useStore((s) => s.processingView);
  const setProcessingView = useStore((s) => s.setProcessingView);
  return processingView === "technical" ? (
    <TechnicalView onSwitch={() => setProcessingView("immersive")} />
  ) : (
    <ImmersiveView onSwitch={() => setProcessingView("technical")} />
  );
}

function ImmersiveView({ onSwitch }) {
  const {
    document, documentId, status, statusMessage, progress, pageStates, draftCharts,
    activePage, reasoningByPage, liveCharts, connectionState, theme, openWorkspace,
  } = useStore();
  const pages = Object.values(pageStates || {}).sort((a, b) => a.page_number - b.page_number);
  const drafts = Object.values(draftCharts || {}).sort((a, b) => a.page_number - b.page_number || a.chart_index - b.chart_index);
  const active = pageStates?.[activePage] || pages.find((p) => !["skipped", "validated", "error"].includes(p.phase)) || pages.at(-1);
  const activeDrafts = drafts.filter((d) => d.page_number === active?.page_number);
  const focusDraft = activeDrafts.at(-1) || (status === "complete" ? drafts.at(-1) : null);
  const focusSeries = (focusDraft?.series || []).filter(Boolean);
  const focusPoints = focusSeries.reduce((count, series) => count + (series.points?.length || 0), 0);
  const reasoning = reasoningByPage?.[active?.page_number] || "";
  const p = progress || {};
  const total = p.pages_total || pages.length || 0;
  const completePages = p.pages_analyzed || 0;
  const pct = total ? Math.round((completePages / total) * 100) : status === "complete" ? 100 : null;
  const currentPhase = status === "complete" ? "validated" : status === "cancelled" ? "cancelled" : active?.phase || (status === "rendering" ? "ready" : "astra");
  const phaseCopy = PHASE_COPY[currentPhase] || [statusMessage || "Preparing", "The pipeline is getting ready."];

  const openResult = async () => {
    const doc = await api.getDocument(documentId);
    openWorkspace(doc);
  };

  return (
    <main className="foundry" aria-busy={!['complete', 'error', 'cancelled'].includes(status)}>
      <div className="foundry-aurora" aria-hidden="true"><i /><i /><i /></div>
      <header className="foundry-head">
        <div>
          <div className="foundry-kicker">Astra visual foundry</div>
          <h2>{document?.name}</h2>
          <div className="foundry-status" aria-live="polite">{statusMessage || phaseCopy[0]}</div>
        </div>
        <div className="foundry-actions">
          <span className={`connection-dot ${connectionState}`} role="status" aria-label={`Live connection: ${connectionState}`} title={`Live connection: ${connectionState}`} />
          <DocumentStopButton />
          <button className="btn btn-ghost btn-sm" onClick={onSwitch}>View technical details</button>
          {["complete", "cancelled"].includes(status) && <button className="btn btn-primary" onClick={openResult}>Open workspace →</button>}
        </div>
      </header>

      <section className="foundry-progress" aria-label="Document extraction progress">
        <div className="foundry-progress-meta">
          <span>{pct == null ? "Discovering pages" : `${completePages} of ${total} pages settled`}</span>
          <b>{pct == null ? "Working" : `${pct}%`}</b>
        </div>
        <div className={`foundry-progress-track ${pct == null ? "indeterminate" : ""}`} role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={pct ?? undefined}>
          <div style={pct == null ? undefined : { width: `${pct}%` }} />
        </div>
      </section>

      <div className="foundry-grid">
        <aside className="page-orbit" aria-label="Pages">
          <div className="foundry-section-label">Document map</div>
          <div className="page-orbit-list">
            {pages.length === 0 && <div className="page-ghost"><span>01</span><em>Preparing pages</em></div>}
            {pages.map((page) => (
              <article key={page.page_number} className={`orbit-page ${page.phase || "ready"} ${page.page_number === active?.page_number ? "active" : ""}`}>
                <div className="orbit-index">{String(page.page_number).padStart(2, "0")}</div>
                <div className="orbit-copy">
                  <strong>{page.label || `Page ${page.page_number}`}</strong>
                  <span>{phaseLabel(page.phase)}</span>
                  {page.gate && !page.gate.bypassed && <small>{Math.round((page.gate.p_charts ?? 0) * 100)}% chart signal · {page.gate.chart_count || 0} predicted</small>}
                </div>
                <div className="orbit-state" aria-hidden="true" />
                {page.phase === "gate" && <div className="scan-line" aria-hidden="true" />}
              </article>
            ))}
          </div>
        </aside>

        <section className={`construction-chamber phase-${currentPhase}`}>
          <div className="chamber-head">
            <div>
              <span className="chamber-page">{active?.label || "Document intake"}</span>
              <h3>{phaseCopy[0]}</h3>
              <p>{phaseCopy[1]}</p>
            </div>
            {focusDraft && <span className={`draft-badge ${focusDraft.status}`}>{focusDraft.status === "validated" ? "Validated" : focusDraft.status === "cancelled" ? "Stopped draft" : `${focusPoints} points live`}</span>}
          </div>

          {connectionState === "disconnected" && <div className="foundry-notice error">Live updates are disconnected. Saved backend progress is safe; reopen this processing view to reconnect.</div>}
          {active?.streamFallback && <div className="foundry-notice">Live plotting is unavailable for this page, so Astra is finishing it normally in the background.</div>}

          <div className={`chamber-visual phase-${currentPhase}`}>
            {active?.image && <img className="source-echo" src={assetUrl(documentId, active.image)} alt="" />}
            <div className="thinking-fog" aria-hidden="true"><i /><i /><i /><i /></div>
            {active?.phase === "gate" && <div className="gate-reticle" aria-hidden="true"><span /><span /><span /><span /></div>}
            {focusDraft && focusDraft.series?.length ? (
              <div className={`draft-chart-shell ${focusDraft.status}`}>
                {focusDraft.status === "draft" && <div className="plot-telemetry"><i />Plotting {focusPoints} points across {focusSeries.length} series</div>}
                <ReactECharts
                  option={specToOption(draftSpec(focusDraft), { theme, scale: 0.9 })}
                  style={{ height: "100%", width: "100%" }}
                  notMerge={false}
                  lazyUpdate
                />
              </div>
            ) : (
              <div className="chamber-core" aria-hidden="true"><span /><span /><span /></div>
            )}
          </div>

          <CognitiveCloud text={reasoning} fallback={active?.summary || phaseCopy[1]} phase={currentPhase} />

          {status === "error" && <div className="foundry-error"><b>Processing paused</b><span>{statusMessage || "The backend could not complete this document."}</span></div>}
        </section>
      </div>

      <section className="result-dock">
        <div className="foundry-section-label">Reconstructed assets · {liveCharts.length}</div>
        <div className="result-dock-list">
          {liveCharts.length === 0 ? <span className="dock-empty">Validated charts will collect here as each page completes.</span> : liveCharts.map((chart) => {
            const spec = chart.versions[chart.current_version].spec;
            return <div className="dock-chart" key={chart.chart_id}><i /><div><strong>{spec.title || chart.chart_id}</strong><span>Page {chart.page_number} · {spec.chart_type}</span></div><b>✓</b></div>;
          })}
        </div>
      </section>
    </main>
  );
}

function TechnicalView({ onSwitch }) {
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
        <button className="btn btn-ghost btn-sm" onClick={onSwitch}>← Visual experience</button>
        <DocumentStopButton />
        {["complete", "cancelled"].includes(status) && (
          <button className="btn btn-primary" onClick={openResult}>
            Open workspace →
          </button>
        )}
        {status === "error" && <span className="pill error"><span className="dot" />Failed</span>}
        {status === "cancelled" && <span className="pill cancelled"><span className="dot" />Stopped</span>}
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
        <Stat num={p.pages_failed || 0} label="Need attention" cls={p.pages_failed ? "err" : ""} />
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
