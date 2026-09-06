import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import * as echarts from "echarts";
import { api, assetUrl } from "../lib/api.js";
import { useStore } from "../store.js";
import { specToOption } from "../lib/chartOption.js";
import { useDraggablePoints } from "../lib/useChartInteractions.js";

const clone = (o) => JSON.parse(JSON.stringify(o));
const PALETTE = ["#5b8cff", "#7c5cff", "#34d399", "#fbbf24", "#f87171", "#22d3ee", "#f472b6", "#a3e635"];

const TABS = ["Data", "Style", "Axes", "Series", "Labels", "Source", "History"];

export default function ChartEditor({ docId, chart }) {
  const updateChart = useStore((s) => s.updateChart);
  const setDocument = useStore((s) => s.setDocument);
  const theme = useStore((s) => s.theme);
  const rightRailOpen = useStore((s) => s.rightRailOpen);
  const setRail = useStore((s) => s.setRail);
  const [tab, setTab] = useState("Data");
  const chartRef = useRef(null);

  const currentSpec = chart.versions[chart.current_version].spec;
  const originalSpec = chart.versions[0].spec;

  // Working draft (uncommitted edits).
  const [draft, setDraft] = useState(() => clone(currentSpec));
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [interactive, setInteractive] = useState(false);
  const [activeSeries, setActiveSeries] = useState(0);
  const [showGrid, setShowGrid] = useState(true);
  const [printStyle, setPrintStyle] = useState(false);
  const [rerunEffort, setRerunEffort] = useState("xhigh");
  const [rerunBusy, setRerunBusy] = useState(false);
  const [rerunError, setRerunError] = useState(null);
  // Geometry: both stage panes keep the ORIGINAL crop's aspect ratio (measured from the
  // crop image itself, so it works for every chart) and all sizes scale with width.
  const [stageW, setStageW] = useState(0);
  const [cropAspect, setCropAspect] = useState(0.62); // height / width
  const stageRef = useRef(null);
  const [view, setView] = useState("side"); // "side" | "overlay" | "only"
  const [opacity, setOpacity] = useState(0.65);

  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setStageW(el.clientWidth));
    ro.observe(el);
    setStageW(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    let alive = true;
    const img = new Image();
    img.onload = () => {
      if (alive && img.naturalWidth > 0) setCropAspect(img.naturalHeight / img.naturalWidth);
    };
    img.src = assetUrl(docId, chart.crop_image);
    return () => {
      alive = false;
    };
  }, [docId, chart.crop_image]);

  // Reset draft whenever the selected chart or its current version changes.
  useEffect(() => {
    setDraft(clone(chart.versions[chart.current_version].spec));
    setDirty(false);
    setActiveSeries(0);
  }, [chart.chart_id, chart.current_version]);

  // Overlay needs the plot-area rectangle the model reports (older charts: Re-extract).
  const hasRect = Array.isArray(draft.plot_rect) && draft.plot_rect.length === 4;
  useEffect(() => {
    if (view === "overlay" && !hasRect) setView("side");
  }, [view, hasRect]);

  // Pane size: side-by-side splits the stage width in two; every mode is capped by the
  // viewport height so the stage fits without scrolling. Whichever limit binds, the
  // aspect ratio stays the crop's. scale=1 corresponds to a ~560px-wide pane.
  const { paneW, paneH, scale, stacked } = useMemo(() => {
    const W = Math.max(280, stageW || 900);
    const maxH = Math.max(260, (typeof window !== "undefined" ? window.innerHeight : 900) - 330);
    const stacked = view === "side" && W < 760;
    const avail = view === "side" && !stacked ? (W - 16) / 2 : W;
    const w = Math.max(240, Math.min(avail, maxH / cropAspect));
    return { paneW: Math.round(w), paneH: Math.max(160, Math.round(w * cropAspect)), scale: w / 560, stacked };
  }, [stageW, cropAspect, view]);

  // `height` lets the renderer size strokes/text from the crop's measured proportions.
  const option = useMemo(
    () => specToOption(draft, { theme, showGrid, scale, width: paneW, height: paneH }),
    [draft, theme, showGrid, scale, paneW, paneH]
  );
  const overlayOpt = useMemo(
    () => specToOption(draft, { theme, showGrid, scale, width: paneW, height: paneH, overlay: true, plotRect: hasRect ? draft.plot_rect : null }),
    [draft, theme, showGrid, scale, paneW, paneH, hasRect]
  );

  const mutate = (fn) => {
    setDraft((d) => {
      const next = clone(d);
      fn(next);
      return next;
    });
    setDirty(true);
  };

  // Stable callbacks for direct mouse manipulation (drag a point / add a point).
  const commitPoint = useCallback((si, pi, patch) => {
    setDraft((d) => {
      const next = clone(d);
      if (next.series[si]?.points[pi]) Object.assign(next.series[si].points[pi], patch);
      return next;
    });
    setDirty(true);
  }, []);

  const addPointToSeries = useCallback((si, point) => {
    setDraft((d) => {
      const next = clone(d);
      if (!next.series[si]) return d;
      next.series[si].points.push(point);
      return next;
    });
    setDirty(true);
  }, []);

  const addSeries = () =>
    mutate((d) => d.series.push({ name: `Line ${d.series.length + 1}`, color: null, conditions: null, points: [] }));

  useDraggablePoints({
    chartRef,
    draft,
    theme,
    enabled: interactive,
    activeSeries,
    onCommitPoint: commitPoint,
    onAddPoint: addPointToSeries,
    refreshKey: view, // the editable chart re-mounts when the view changes
  });

  const saveVersion = async () => {
    setBusy(true);
    try {
      const updated = await api.saveVersion(docId, chart.chart_id, draft, null);
      updateChart(updated);
      setDirty(false);
    } finally {
      setBusy(false);
    }
  };

  const revert = async () => {
    setBusy(true);
    try {
      const updated = await api.revert(docId, chart.chart_id);
      updateChart(updated);
    } finally {
      setBusy(false);
    }
  };

  const jumpToVersion = async (v) => {
    setBusy(true);
    try {
      const updated = await api.setVersion(docId, chart.chart_id, v);
      updateChart(updated);
    } finally {
      setBusy(false);
    }
  };

  const finalize = async () => {
    setBusy(true);
    try {
      const updated = await api.finalize(docId, chart.chart_id);
      updateChart(updated);
    } finally {
      setBusy(false);
    }
  };

  const resetDraft = () => {
    setDraft(clone(currentSpec));
    setDirty(false);
  };

  // Re-run extraction for this chart only (higher effort). Appends a new version;
  // the editor's reset effect then loads it as the current draft.
  const rerun = async () => {
    setRerunBusy(true);
    setRerunError(null);
    try {
      const updated = await api.rerunChart(docId, chart.chart_id, { effort: rerunEffort });
      updateChart(updated);
      // refresh document-level usage/cost (the re-run is added to the totals)
      api.getDocument(docId).then(setDocument).catch(() => {});
    } catch (e) {
      setRerunError(e?.response?.data?.detail || "Re-extraction failed");
    } finally {
      setRerunBusy(false);
    }
  };

  // ---- export ----
  // WYSIWYG by default: reproduce EXACTLY what's on screen — same chart option
  // (theme, colors, gridlines, inline labels, annotations), same size, same
  // background. We render from the spec into an offscreen instance (matched to
  // the live chart's dimensions) so the output excludes editing-only overlays
  // (drag handles) and hover tooltips, but is otherwise pixel-faithful.
  // `printStyle` optionally swaps to the clean white-background print look.
  const screenBg = theme === "dark" ? "#0a0e17" : "#f5f7fc"; // matches CSS --bg-0

  const renderExport = (renderer) => {
    // same size and aspect as the on-screen pane (works in every view mode)
    const w = paneW;
    const h = paneH;
    const div = document.createElement("div");
    div.style.cssText = `width:${w}px;height:${h}px;position:absolute;left:-99999px;top:0;`;
    document.body.appendChild(div);
    const inst = echarts.init(div, null, { renderer });
    const exportScale = w / 560; // same proportions as on screen
    const opt = printStyle
      ? { ...specToOption(draft, { theme, forExport: true, showGrid, scale: exportScale, width: w, height: h }), animation: false }
      : { ...specToOption(draft, { theme, showGrid, scale: exportScale, width: w, height: h }), backgroundColor: screenBg, animation: false };
    inst.setOption(opt);
    return { inst, cleanup: () => { inst.dispose(); div.remove(); } };
  };
  const exportImage = (type) => {
    const { inst, cleanup } = renderExport("canvas");
    const url = inst.getDataURL({
      type: type === "jpeg" ? "jpeg" : "png",
      pixelRatio: 3, // crisp, high-resolution; proportions stay identical to screen
      backgroundColor: printStyle ? "#ffffff" : screenBg,
    });
    cleanup();
    downloadURL(url, `${draft.title || chart.chart_id}.${type}`);
  };
  const exportSVG = () => {
    const { inst, cleanup } = renderExport("svg");
    let svg = null;
    try {
      svg = inst.renderToSVGString ? inst.renderToSVGString() : null;
    } catch (_) {}
    cleanup();
    if (svg) downloadURL("data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg), `${draft.title || chart.chart_id}.svg`);
    else exportImage("png");
  };
  const exportData = (fmt) => {
    if (fmt === "json") {
      downloadURL("data:application/json," + encodeURIComponent(JSON.stringify(draft, null, 2)), `${chart.chart_id}.json`);
    } else {
      // CSV: series as columns
      const rows = [];
      const header = ["x/label", ...draft.series.map((s) => s.name)];
      const maxLen = Math.max(...draft.series.map((s) => s.points.length), 0);
      for (let i = 0; i < maxLen; i++) {
        const first = draft.series[0]?.points[i];
        const key = first?.label ?? first?.x ?? i;
        rows.push([key, ...draft.series.map((s) => s.points[i]?.y ?? "")]);
      }
      const csv = [header, ...rows].map((r) => r.join(",")).join("\n");
      downloadURL("data:text/csv;charset=utf-8," + encodeURIComponent(csv), `${chart.chart_id}.csv`);
    }
  };

  return (
    <div className="ws-editor">
      {/* ---- centre stage: original | reconstructed ---- */}
      <section className="stage">
        <div className="stage-head">
          <div>
            <div style={{ fontSize: 16, fontWeight: 650 }}>{draft.title || chart.chart_id}</div>
            <div className="muted">
              Page {chart.page_number} · v{chart.current_version} of {chart.versions.length - 1}
              {chart.final_version != null && <span className="chip" style={{ marginLeft: 8 }}>finalized v{chart.final_version}</span>}
            </div>
          </div>
          <div className="spacer" />
          <div
            className="inline"
            style={{ gap: 6, marginRight: 12 }}
            title="Re-run extraction for THIS chart at a higher reasoning effort. Adds a new version; the original v0 stays untouched."
          >
            <select
              className="input"
              style={{ width: "auto", padding: "6px 9px", fontSize: 12 }}
              value={rerunEffort}
              onChange={(e) => setRerunEffort(e.target.value)}
              disabled={rerunBusy}
            >
              <option value="high">effort: high</option>
              <option value="xhigh">effort: xhigh</option>
              <option value="max">effort: max</option>
            </select>
            <button className="btn btn-sm" onClick={rerun} disabled={rerunBusy || busy}>
              {rerunBusy ? (
                <>
                  <span className="pipe-spin" style={{ display: "inline-block" }} /> Re-extracting…
                </>
              ) : (
                "↻ Re-extract"
              )}
            </button>
          </div>
          {dirty && <span className="dirty-badge">● unsaved edits</span>}
        </div>
        {rerunError && <div className="warn-banner">⚠ {rerunError}</div>}

        {/* view + interaction toolbar */}
        <div className="stage-toolbar">
          <div className="seg" title="How to show the original and the reconstruction">
            {[
              ["side", "Side by side"],
              ["overlay", "Overlay"],
              ["only", "Chart only"],
            ].map(([m, label]) => (
              <button
                key={m}
                className={`seg-btn ${view === m ? "active" : ""}`}
                disabled={m === "overlay" && !hasRect}
                title={m === "overlay" && !hasRect ? "Re-extract this chart once to capture the plot-area position needed for the overlay" : ""}
                onClick={() => setView(m)}
              >
                {label}
              </button>
            ))}
          </div>
          {view === "overlay" && (
            <label className="inline" style={{ gap: 6, fontSize: 12 }}>
              opacity
              <input type="range" min="0.1" max="1" step="0.05" value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} />
            </label>
          )}
          <div className="spacer" />
          <button
            className={`btn btn-sm ${interactive ? "btn-primary" : ""}`}
            onClick={() => setInteractive((v) => !v)}
            disabled={draft.chart_type === "pie"}
            title={draft.chart_type === "pie" ? "Drag editing isn't available for pie charts" : "Toggle direct mouse editing"}
          >
            {interactive ? "✓ Interactive edit on" : "✋ Interactive edit"}
          </button>
          {interactive && draft.chart_type !== "pie" && (
            <>
              <span className="muted">drag points · double-click to add</span>
              {draft.series.length > 1 && (
                <select
                  className="input"
                  style={{ width: "auto" }}
                  value={activeSeries}
                  onChange={(e) => setActiveSeries(Number(e.target.value))}
                >
                  {draft.series.map((s, i) => (
                    <option key={i} value={i}>
                      add to: {s.name}
                    </option>
                  ))}
                </select>
              )}
            </>
          )}
          {draft.chart_type === "pie" && <span className="muted">drag editing not available for pie</span>}
          <button
            className={`btn btn-sm ${showGrid ? "btn-primary" : ""}`}
            onClick={() => setShowGrid((v) => !v)}
            title="Show / hide gridlines (applies on screen and in exports)"
          >
            {showGrid ? "▦ Gridlines on" : "▦ Gridlines off"}
          </button>
          <button className="btn btn-sm" onClick={addSeries}>+ Add line / series</button>
        </div>

        {/* the two panes: identical size and aspect ratio (the crop's) */}
        <div className={`stage-body ${stacked ? "stacked" : ""}`} ref={stageRef}>
          {view !== "only" && (
            <div className="pane" style={{ width: paneW }}>
              <div className="pane-cap">
                <span>Original (PDF)</span>
                {view === "overlay" && <span>+ our curves at {Math.round(opacity * 100)}%</span>}
              </div>
              <div className="pane-box" style={{ height: paneH }}>
                <img className="cmp-layer" src={assetUrl(docId, chart.crop_image)} alt="original chart" />
                {view === "overlay" && (
                  <div className="cmp-layer" style={{ opacity, pointerEvents: "none" }}>
                    <ReactECharts option={overlayOpt} style={{ width: "100%", height: "100%" }} notMerge lazyUpdate />
                  </div>
                )}
              </div>
            </div>
          )}
          {view !== "overlay" && (
            <div className="pane" style={{ width: paneW }}>
              <div className="pane-cap">
                <span>Reconstructed · editable</span>
                {interactive && draft.chart_type !== "pie" && <span>drag points · double-click adds</span>}
              </div>
              <div className="pane-box chart-canvas" style={{ height: paneH }}>
                <ReactECharts
                  ref={chartRef}
                  option={option}
                  style={{ width: "100%", height: "100%" }}
                  notMerge
                  lazyUpdate
                  opts={{ renderer: "canvas" }}
                />
              </div>
            </div>
          )}
        </div>
        {view === "overlay" && (
          <div className="muted">Our curves drawn over the PDF crop at the same size — any offset you see is real. Edit, or re-extract.</div>
        )}
        {!hasRect && (
          <div className="muted">Overlay needs the plot-area position: press ↻ Re-extract once on this chart to capture it.</div>
        )}

        {/* export */}
        <div className="stage-export">
          <span className="muted">Export:</span>
          <button className="btn btn-sm" onClick={() => exportImage("png")}>PNG</button>
          <button className="btn btn-sm" onClick={() => exportImage("jpeg")}>JPEG</button>
          <button className="btn btn-sm" onClick={exportSVG}>SVG</button>
          <button className="btn btn-sm" onClick={() => exportData("csv")}>CSV</button>
          <button className="btn btn-sm" onClick={() => exportData("json")}>JSON</button>
          <label
            className="inline"
            style={{ marginLeft: "auto", fontSize: 12, gap: 6, cursor: "pointer" }}
            title="Off = export looks exactly like the chart on screen. On = clean white background for print."
          >
            <input type="checkbox" checked={printStyle} onChange={(e) => setPrintStyle(e.target.checked)} />
            Print style (white bg)
          </label>
        </div>
      </section>

      {/* ---- right rail: tabbed editor (collapses to a vertical tab strip) ---- */}
      <aside className={`rail rail-right ${rightRailOpen ? "" : "collapsed"}`}>
        {rightRailOpen ? (
          <>
            <div className="rail-head">
              <span>Editor{dirty ? " · unsaved" : ""}</span>
              <button className="rail-toggle" onClick={() => setRail("right", false)} title="Collapse editor  ]">
                ▸
              </button>
            </div>
            <div className="tabs">
              {TABS.map((t) => (
                <div key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
                  {t}
                </div>
              ))}
            </div>
            <div className="tab-body">
              {tab === "Data" && <DataTab draft={draft} mutate={mutate} addSeries={addSeries} />}
              {tab === "Style" && <StyleTab draft={draft} mutate={mutate} />}
              {tab === "Axes" && <AxesTab draft={draft} mutate={mutate} />}
              {tab === "Series" && <SeriesTab draft={draft} mutate={mutate} />}
              {tab === "Labels" && <LabelsTab draft={draft} mutate={mutate} />}
              {tab === "Source" && <SourceTab chart={chart} />}
              {tab === "History" && (
                <HistoryTab chart={chart} onJump={jumpToVersion} onFinalize={finalize} busy={busy} />
              )}
            </div>
            <div className="editor-actions">
              <button className="btn btn-primary btn-sm" disabled={!dirty || busy} onClick={saveVersion}>
                Save as new version
              </button>
              <button className="btn btn-sm" disabled={!dirty} onClick={resetDraft}>
                Discard
              </button>
              <button className="btn btn-sm" disabled={chart.current_version === 0 || busy} onClick={revert}>
                ↶ Revert one
              </button>
            </div>
          </>
        ) : (
          <div className="rail-strip">
            <button className="rail-toggle" onClick={() => setRail("right", true)} title="Expand editor  ]">
              ◂
            </button>
            {TABS.map((t) => (
              <button
                key={t}
                className={`vtab ${tab === t ? "active" : ""}`}
                onClick={() => {
                  setTab(t);
                  setRail("right", true);
                }}
                title={`Open ${t}`}
              >
                {t}
              </button>
            ))}
            {dirty && <span className="vdot" title="Unsaved edits" />}
          </div>
        )}
      </aside>
    </div>
  );
}

/* ------------------------------------------------------------------ tabs */
function DataTab({ draft, mutate, addSeries }) {
  return (
    <>
      <div className="muted" style={{ marginBottom: 12 }}>
        Edit any value — the chart updates instantly. Values are model estimates unless printed on the source.
      </div>
      {draft.series.map((s, si) => (
        <div key={si} className="series-block">
          <div className="series-head">
            <span style={{ width: 10, height: 10, borderRadius: 3, background: s.color || PALETTE[si % PALETTE.length] }} />
            <strong style={{ fontSize: 13 }}>{s.name}</strong>
          </div>
          <table className="data-table">
            <thead>
              <tr>
                <th>{s.points.some((p) => p.label != null) ? "Label" : "X"}</th>
                <th>Y</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {s.points.map((p, pi) => (
                <tr key={pi}>
                  <td>
                    {p.label != null ? (
                      <input value={p.label ?? ""} onChange={(e) => mutate((d) => (d.series[si].points[pi].label = e.target.value))} />
                    ) : (
                      <input
                        type="number"
                        value={p.x ?? ""}
                        onChange={(e) => mutate((d) => (d.series[si].points[pi].x = numOrNull(e.target.value)))}
                      />
                    )}
                  </td>
                  <td>
                    <input
                      type="number"
                      value={p.y ?? ""}
                      onChange={(e) => mutate((d) => (d.series[si].points[pi].y = numOrNull(e.target.value)))}
                    />
                  </td>
                  <td>
                    <button className="btn btn-ghost btn-sm" onClick={() => mutate((d) => d.series[si].points.splice(pi, 1))}>✕</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button
            className="btn btn-sm"
            style={{ marginTop: 8 }}
            onClick={() =>
              mutate((d) => {
                const last = d.series[si].points[d.series[si].points.length - 1] || {};
                d.series[si].points.push(last.label != null ? { label: "new", y: 0 } : { x: 0, y: 0 });
              })
            }
          >
            + Add point
          </button>
        </div>
      ))}
      <button className="btn btn-sm btn-primary" style={{ width: "100%" }} onClick={addSeries}>
        + Add new line / series
      </button>
    </>
  );
}

function StyleTab({ draft, mutate }) {
  return (
    <>
      <div className="field">
        <label>Chart type</label>
        <select className="input" value={draft.chart_type} onChange={(e) => mutate((d) => (d.chart_type = e.target.value))}>
          {["line", "area", "bar", "scatter", "pie"].map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
      </div>
      <div className="field inline">
        <input type="checkbox" checked={draft.stacked} onChange={(e) => mutate((d) => (d.stacked = e.target.checked))} />
        <label className="mb0" style={{ margin: 0 }}>Stacked (bar)</label>
      </div>
      <div className="field inline">
        <input type="checkbox" checked={draft.legend} onChange={(e) => mutate((d) => (d.legend = e.target.checked))} />
        <label className="mb0" style={{ margin: 0 }}>Show legend</label>
      </div>
      <div className="field">
        <label>Title</label>
        <input className="input" value={draft.title ?? ""} onChange={(e) => mutate((d) => (d.title = e.target.value))} />
      </div>
      <div className="field">
        <label>Subtitle</label>
        <input className="input" value={draft.subtitle ?? ""} onChange={(e) => mutate((d) => (d.subtitle = e.target.value))} />
      </div>
      <label>Series colors</label>
      {draft.series.map((s, si) => (
        <div key={si} className="inline" style={{ marginBottom: 8 }}>
          <input
            type="color"
            className="color-swatch"
            value={s.color || PALETTE[si % PALETTE.length]}
            onChange={(e) => mutate((d) => (d.series[si].color = e.target.value))}
          />
          <span style={{ fontSize: 12 }}>{s.name}</span>
        </div>
      ))}
    </>
  );
}

function AxisFields({ axis, onChange }) {
  return (
    <>
      <div className="row2">
        <div className="field">
          <label>Label</label>
          <input className="input" value={axis.label ?? ""} onChange={(e) => onChange({ ...axis, label: e.target.value })} />
        </div>
        <div className="field">
          <label>Unit</label>
          <input className="input" value={axis.unit ?? ""} onChange={(e) => onChange({ ...axis, unit: e.target.value })} />
        </div>
      </div>
      <div className="row2">
        <div className="field">
          <label>Scale</label>
          <select className="input" value={axis.scale} onChange={(e) => onChange({ ...axis, scale: e.target.value })}>
            <option value="linear">linear</option>
            <option value="log">log</option>
          </select>
        </div>
        <div className="field">
          <label>Min / Max</label>
          <div className="row2">
            <input className="input" type="number" placeholder="auto" value={axis.min ?? ""} onChange={(e) => onChange({ ...axis, min: numOrNull(e.target.value) })} />
            <input className="input" type="number" placeholder="auto" value={axis.max ?? ""} onChange={(e) => onChange({ ...axis, max: numOrNull(e.target.value) })} />
          </div>
        </div>
      </div>
      <div className="field inline">
        <input type="checkbox" checked={axis.inverse === true} onChange={(e) => onChange({ ...axis, inverse: e.target.checked })} />
        <label className="mb0" style={{ margin: 0 }} title="Values decrease along the axis: up for Y (e.g. 0 at the bottom, -1 at the top), right for X">
          Inverted direction (values decrease upward / rightward)
        </label>
      </div>
      {axis.scale !== "log" && (
        <div className="row2">
          <div className="field">
            <label>Major tick step</label>
            <input className="input" type="number" placeholder="auto" value={axis.major_interval ?? ""} onChange={(e) => onChange({ ...axis, major_interval: numOrNull(e.target.value) })} />
          </div>
          <div className="field">
            <label>Minor grid step</label>
            <input className="input" type="number" placeholder="none" value={axis.minor_interval ?? ""} onChange={(e) => onChange({ ...axis, minor_interval: numOrNull(e.target.value) })} />
          </div>
        </div>
      )}
    </>
  );
}

function AxesTab({ draft, mutate }) {
  return (
    <>
      <div className="section-title" style={{ marginTop: 0 }}>X axis</div>
      <AxisFields axis={draft.x_axis} onChange={(ax) => mutate((d) => (d.x_axis = ax))} />
      <div className="section-title">Y axis</div>
      <AxisFields axis={draft.y_axis} onChange={(ax) => mutate((d) => (d.y_axis = ax))} />
    </>
  );
}

function SeriesTab({ draft, mutate }) {
  return (
    <>
      {draft.series.map((s, si) => (
        <div key={si} className="series-block">
          <div className="row2">
            <div className="field mb0">
              <label>Name</label>
              <input className="input" value={s.name} onChange={(e) => mutate((d) => (d.series[si].name = e.target.value))} />
            </div>
            <div className="field mb0">
              <label>Conditions</label>
              <input className="input" value={s.conditions ?? ""} onChange={(e) => mutate((d) => (d.series[si].conditions = e.target.value))} />
            </div>
          </div>
          <div className="row2" style={{ marginTop: 8 }}>
            <div className="field mb0">
              <label>Label X (on plot)</label>
              <input className="input" type="number" placeholder="line end" value={s.label_x ?? ""} onChange={(e) => mutate((d) => (d.series[si].label_x = numOrNull(e.target.value)))} />
            </div>
            <div className="field mb0">
              <label>Label Y (on plot)</label>
              <input className="input" type="number" placeholder="line end" value={s.label_y ?? ""} onChange={(e) => mutate((d) => (d.series[si].label_y = numOrNull(e.target.value)))} />
            </div>
          </div>
          <div className="row2" style={{ marginTop: 8 }}>
            <div className="field inline mb0" style={{ alignSelf: "end" }}>
              <input type="checkbox" checked={s.label_boxed === true} onChange={(e) => mutate((d) => (d.series[si].label_boxed = e.target.checked))} />
              <label className="mb0" style={{ margin: 0 }}>Boxed label</label>
            </div>
            <div className="field mb0">
              <label>Line weight</label>
              <select className="input" value={s.line_width ?? "medium"} onChange={(e) => mutate((d) => (d.series[si].line_width = e.target.value))}>
                <option value="thin">thin</option>
                <option value="medium">medium</option>
                <option value="thick">thick</option>
              </select>
            </div>
          </div>
          <button className="btn btn-sm btn-danger" style={{ marginTop: 10 }} onClick={() => mutate((d) => d.series.splice(si, 1))}>
            Remove series
          </button>
        </div>
      ))}
      <button
        className="btn btn-sm"
        onClick={() => mutate((d) => d.series.push({ name: `Series ${d.series.length + 1}`, color: null, conditions: null, points: [{ x: 0, y: 0 }] }))}
      >
        + Add series
      </button>
    </>
  );
}

function LabelsTab({ draft, mutate }) {
  const anns = draft.annotations || [];
  return (
    <>
      <div className="muted" style={{ marginBottom: 12 }}>
        Reproduce the source chart's on-plot text: labels next to each line and note boxes.
      </div>
      <div className="field inline">
        <input
          type="checkbox"
          checked={draft.inline_labels !== false}
          onChange={(e) => mutate((d) => (d.inline_labels = e.target.checked))}
        />
        <label className="mb0" style={{ margin: 0 }}>Inline curve labels (name at line end)</label>
      </div>
      <div className="field inline">
        <input
          type="checkbox"
          checked={draft.show_markers === true}
          onChange={(e) => mutate((d) => (d.show_markers = e.target.checked))}
        />
        <label className="mb0" style={{ margin: 0 }}>Show point markers</label>
      </div>
      <div className="field inline">
        <input
          type="checkbox"
          checked={draft.smooth !== false}
          onChange={(e) => mutate((d) => (d.smooth = e.target.checked))}
        />
        <label className="mb0" style={{ margin: 0 }}>Smooth curves — shape-preserving, no overshoot (off for straight-segment plots)</label>
      </div>

      <div className="section-title" style={{ marginTop: 6 }}>Note boxes</div>
      {anns.length === 0 && (
        <div className="muted" style={{ marginBottom: 10 }}>None. Add a box to reproduce an in-plot note.</div>
      )}
      {anns.map((a, ai) => (
        <div key={ai} className="series-block">
          <div className="field mb0">
            <label>Text</label>
            <textarea
              className="input"
              rows={3}
              value={a.text}
              onChange={(e) => mutate((d) => (d.annotations[ai].text = e.target.value))}
            />
          </div>
          <div className="row2" style={{ marginTop: 8 }}>
            <div className="field mb0">
              <label>Position</label>
              <select
                className="input"
                value={a.position || "top-left"}
                onChange={(e) => mutate((d) => (d.annotations[ai].position = e.target.value))}
              >
                {["top-left", "top-right", "bottom-left", "bottom-right"].map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>
            <div className="field mb0" style={{ display: "flex", alignItems: "flex-end" }}>
              <button className="btn btn-sm btn-danger" onClick={() => mutate((d) => d.annotations.splice(ai, 1))}>
                Remove
              </button>
            </div>
          </div>
        </div>
      ))}
      <button
        className="btn btn-sm"
        onClick={() =>
          mutate((d) => {
            if (!d.annotations) d.annotations = [];
            d.annotations.push({ text: "Note:", position: "top-left" });
          })
        }
      >
        + Add note box
      </button>
    </>
  );
}

function SourceTab({ chart }) {
  // Show the confidence of the version being viewed (so a re-run's scores are visible).
  const cur = chart.versions[chart.current_version]?.spec || {};
  const c = cur.confidence || chart.confidence || {};
  const bar = (label, v) => {
    const val = Math.round((v ?? 0) * 100);
    const col = val >= 80 ? "#34d399" : val >= 60 ? "#fbbf24" : "#f87171";
    return (
      <div className="conf-bar" key={label}>
        <div className="lbl"><span>{label}</span><span>{val}%</span></div>
        <div className="conf-track"><div className="conf-fill" style={{ width: `${val}%`, background: col }} /></div>
      </div>
    );
  };
  const notes = cur.notes ?? chart.versions[0].spec.notes;
  return (
    <>
      <div className="section-title" style={{ marginTop: 0 }}>Extraction confidence</div>
      {bar("Overall", c.overall)}
      {bar("Axes", c.axis)}
      {bar("Legend", c.legend)}
      {bar("Data values", c.data)}
      {notes && (
        <>
          <div className="section-title">Model notes</div>
          <div className="muted">{notes}</div>
        </>
      )}
      <div className="warn-banner" style={{ marginTop: 14 }}>
        Values read from a plotted curve are estimates. Compare against the original (left) and correct as needed.
      </div>
    </>
  );
}

function HistoryTab({ chart, onJump, onFinalize, busy }) {
  return (
    <>
      <div className="muted" style={{ marginBottom: 12 }}>
        Version 0 (original extraction) is immutable. Click any version to restore it, or finalize the current one.
      </div>
      {chart.versions.map((v) => (
        <div
          key={v.version}
          className={`version-item ${v.version === chart.current_version ? "current" : ""} ${v.kind === "original" ? "original" : ""}`}
          onClick={() => onJump(v.version)}
        >
          <div>
            <div className="vlabel">v{v.version} · {v.label}</div>
            <div className="vmeta">{v.kind}{chart.final_version === v.version ? " · finalized" : ""}</div>
          </div>
          {v.version === chart.current_version && <span className="chip">current</span>}
        </div>
      ))}
      <button className="btn btn-primary btn-sm" style={{ marginTop: 12, width: "100%" }} disabled={busy} onClick={onFinalize}>
        ✓ Finalize current version
      </button>
    </>
  );
}

/* ------------------------------------------------------------------ utils */
function numOrNull(v) {
  if (v === "" || v == null) return null;
  const n = Number(v);
  return Number.isNaN(n) ? null : n;
}
function downloadURL(url, filename) {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}
