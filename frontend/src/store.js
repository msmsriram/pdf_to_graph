import { create } from "zustand";

const initialTheme =
  (typeof localStorage !== "undefined" && localStorage.getItem("pcs-theme")) || "light";

// Remembered rail state; first visit: open on wide screens, collapsed on narrow ones.
function railDefault(key) {
  try {
    const v = localStorage.getItem(key);
    if (v === "1") return true;
    if (v === "0") return false;
  } catch (_) {}
  return typeof window === "undefined" ? true : window.innerWidth >= 1280;
}

const emptyAxis = () => ({ label: null, unit: null, scale: "linear", min: null, max: null, inverse: false, major_interval: null, minor_interval: null });

function manifestPageState(page) {
  let phase = "ready";
  if (page.status === "skipped") phase = "skipped";
  else if (page.status === "error") phase = "error";
  else if (page.status === "analyzed") phase = "validated";
  return { ...page, phase };
}

// Global UI state: which view, the active document, and its live-updating charts.
export const useStore = create((set, get) => ({
  theme: initialTheme, // "light" | "dark"
  toggleTheme: () =>
    set((s) => {
      const theme = s.theme === "dark" ? "light" : "dark";
      try {
        localStorage.setItem("pcs-theme", theme);
      } catch (_) {}
      return { theme };
    }),

  view: "home", // "home" | "processing" | "workspace"
  documentId: null,
  document: null, // full manifest
  liveCharts: [], // charts as they stream in during processing
  progress: null,
  status: null,
  statusMessage: "",
  logs: [],
  pageStates: {},
  draftCharts: {},
  activePage: null,
  reasoningByPage: {},
  lastSequence: 0,
  connectionState: "closed",
  documentJob: null,
  chartJobs: {},
  processingView: "immersive", // "immersive" | "technical"
  setProcessingView: (processingView) => set({ processingView }),
  setConnectionState: (connectionState) => set({ connectionState }),
  selectedChartId: null,
  // Token/cost accounting streamed during processing (per page + running totals).
  usageLive: null,
  costOpen: false,
  toggleCost: () => set((s) => ({ costOpen: !s.costOpen })),

  // Workspace side rails (chart list on the left, editor on the right): open by
  // default on wide screens, collapsed on narrow ones, and remembered.
  leftRailOpen: railDefault("pcs-rail-left"),
  rightRailOpen: railDefault("pcs-rail-right"),
  setRail: (side, open) =>
    set(() => {
      try {
        localStorage.setItem(side === "left" ? "pcs-rail-left" : "pcs-rail-right", open ? "1" : "0");
      } catch (_) {}
      return side === "left" ? { leftRailOpen: open } : { rightRailOpen: open };
    }),
  toggleRail: (side) => {
    const s = get();
    s.setRail(side, !(side === "left" ? s.leftRailOpen : s.rightRailOpen));
  },

  goHome: () =>
    set({
      view: "home", documentId: null, document: null, liveCharts: [], logs: [], selectedChartId: null,
      usageLive: null, costOpen: false, pageStates: {}, draftCharts: {}, activePage: null,
      reasoningByPage: {}, lastSequence: 0, connectionState: "closed", processingView: "immersive",
      documentJob: null, chartJobs: {},
    }),

  startProcessing: (documentId, name, jobId = null) =>
    set({
      view: "processing",
      documentId,
      document: { document_id: documentId, name, status: "uploaded" },
      liveCharts: [],
      progress: { pages_total: 0, pages_rendered: 0, pages_analyzed: 0, charts_detected: 0, charts_extracted: 0 },
      status: "uploaded",
      statusMessage: "Uploaded, starting…",
      logs: [],
      usageLive: null,
      pageStates: {},
      draftCharts: {},
      activePage: null,
      reasoningByPage: {},
      lastSequence: 0,
      connectionState: "connecting",
      processingView: "immersive",
      documentJob: jobId ? { job_id: jobId, status: "running", scope: "document" } : null,
      chartJobs: {},
    }),

  openWorkspace: (doc) =>
    set(() => {
      const chartJobs = Object.fromEntries(
        (doc.active_jobs || [])
          .filter((job) => job.scope === "chart" && job.chart_id)
          .map((job) => [job.chart_id, { ...job, reasoning: "" }]),
      );
      const activeChartId = Object.keys(chartJobs)[0];
      return {
        view: "workspace",
        documentId: doc.document_id,
        document: doc,
        liveCharts: doc.charts || [],
        selectedChartId: activeChartId || (doc.charts && doc.charts[0]?.chart_id) || null,
        status: doc.status,
        lastSequence: 0,
        documentJob: null,
        chartJobs,
      };
    }),

  beginChartJob: (chartId, job) =>
    set((st) => {
      const existing = st.chartJobs[chartId];
      const next = existing?.job_id === job.job_id
        ? { ...job, ...existing, chart_id: chartId }
        : { ...job, chart_id: chartId, status: "running", reasoning: "" };
      return { chartJobs: { ...st.chartJobs, [chartId]: next } };
    }),

  handleEvent: (ev) => {
    const s = get();
    const sequence = Number(ev.sequence) || 0;
    if (sequence && sequence <= s.lastSequence) return;
    if (sequence) set({ lastSequence: sequence });
    const patchPage = (pageNumber, patch) => {
      if (!pageNumber) return;
      set((st) => ({
        pageStates: {
          ...st.pageStates,
          [pageNumber]: { ...(st.pageStates[pageNumber] || { page_number: pageNumber, label: `Page ${pageNumber}` }), ...patch },
        },
      }));
    };
    const patchDraft = (draftId, updater) => {
      if (!draftId) return;
      set((st) => {
        const current = st.draftCharts[draftId];
        if (!current) return {};
        return { draftCharts: { ...st.draftCharts, [draftId]: updater(current) } };
      });
    };
    const patchChartJob = (chartId, patch) => {
      if (!chartId) return;
      set((st) => ({
        chartJobs: {
          ...st.chartJobs,
          [chartId]: { ...(st.chartJobs[chartId] || { chart_id: chartId }), ...patch },
        },
      }));
    };
    switch (ev.type) {
      case "job_started":
        if (ev.scope === "document") set({ documentJob: { job_id: ev.job_id, scope: "document", status: "running" } });
        else patchChartJob(ev.chart_id, { job_id: ev.job_id, draft_id: ev.draft_id, status: "running" });
        break;
      case "job_cancel_requested":
        if (ev.scope === "document") set((st) => ({ documentJob: { ...(st.documentJob || {}), job_id: ev.job_id, status: "cancelling" } }));
        else patchChartJob(ev.chart_id, { status: "cancelling" });
        break;
      case "job_cancelled":
        if (ev.scope === "document") set((st) => ({
          documentJob: { ...(st.documentJob || {}), job_id: ev.job_id, status: "cancelled" },
          draftCharts: Object.fromEntries(Object.entries(st.draftCharts).map(([id, draft]) => [id, draft.status === "draft" ? { ...draft, status: "cancelled" } : draft])),
        }));
        else patchChartJob(ev.chart_id, { status: "cancelled" });
        break;
      case "status":
        set({ status: ev.status, statusMessage: ev.message });
        break;
      case "progress":
        set({ progress: ev.progress });
        break;
      case "log":
        set({ logs: [...s.logs.slice(-80), { level: ev.level, message: ev.message, t: Date.now() }] });
        break;
      case "page_rendered":
        patchPage(ev.page_number, { ...ev, phase: "ready" });
        break;
      case "gate_started":
        set({ activePage: ev.page_number });
        patchPage(ev.page_number, { label: ev.label, phase: "gate", gateModel: ev.model });
        break;
      case "gate_result":
        patchPage(ev.page_number, { label: ev.label, phase: ev.skipped ? "skipped" : "passed", gate: ev });
        break;
      case "gate_bypassed":
        patchPage(ev.page_number, { label: ev.label, phase: "passed", gate: { bypassed: true, reason: ev.reason } });
        break;
      case "page_skipped":
        patchPage(ev.page_number, { phase: "skipped" });
        break;
      case "astra_started":
        set({ activePage: ev.page_number });
        patchPage(ev.page_number, { label: ev.label, phase: "astra", model: ev.model, effort: ev.effort });
        break;
      case "reasoning_summary_delta":
        set((st) => ({
          reasoningByPage: {
            ...st.reasoningByPage,
            [ev.page_number]: `${st.reasoningByPage[ev.page_number] || ""}${ev.delta || ""}`.slice(-1200),
          },
        }));
        break;
      case "astra_output_started":
        patchPage(ev.page_number, { phase: "streaming" });
        break;
      case "page_summary_draft":
        patchPage(ev.page_number, { phase: "streaming", summary: ev.summary });
        break;
      case "chart_draft_started": {
        const draft = {
          draft_id: ev.draft_id, page_number: ev.page_number, chart_index: ev.chart_index,
          title: ev.title, chart_type: ev.chart_type, status: "draft", x_axis: emptyAxis(), y_axis: emptyAxis(), series: [],
        };
        set((st) => ({ draftCharts: { ...st.draftCharts, [ev.draft_id]: draft } }));
        patchPage(ev.page_number, { phase: "constructing" });
        break;
      }
      case "chart_draft_axes":
        patchDraft(ev.draft_id, (d) => ({ ...d, x_axis: ev.x_axis, y_axis: ev.y_axis }));
        break;
      case "series_draft_started":
        patchDraft(ev.draft_id, (d) => {
          const series = [...d.series];
          series[ev.series_index] = series[ev.series_index] || { name: ev.name, color: null, conditions: null, points: [] };
          return { ...d, series };
        });
        break;
      case "points_draft_batch":
        patchDraft(ev.draft_id, (d) => {
          const series = [...d.series];
          const current = series[ev.series_index] || { name: `Series ${ev.series_index + 1}`, color: null, conditions: null, points: [] };
          series[ev.series_index] = { ...current, points: [...current.points, ...(ev.points || [])] };
          return { ...d, series };
        });
        break;
      case "chart_validated":
        patchDraft(ev.draft_id, (d) => {
          const spec = ev.chart?.versions?.[ev.chart.current_version]?.spec;
          return { ...d, ...(spec || {}), status: "validated", finalChartId: ev.chart?.chart_id };
        });
        break;
      case "chart_extracted":
        set({ liveCharts: [...s.liveCharts.filter((c) => c.chart_id !== ev.chart.chart_id), ev.chart] });
        break;
      case "page_validated":
      case "page_analyzed":
        patchPage(ev.page_number, { phase: "validated", summary: ev.summary, charts_found: ev.charts_found });
        break;
      case "page_failed":
        patchPage(ev.page_number, { phase: "error", error: ev.message, label: ev.label });
        break;
      case "astra_stream_fallback":
        patchPage(ev.page_number, { streamFallback: true });
        break;
      case "chart_rerun_started":
        patchChartJob(ev.chart_id, {
          job_id: ev.job_id, draft_id: ev.draft_id, status: "running", reasoning: "",
          model: ev.model, effort: ev.effort, error: null,
        });
        break;
      case "chart_rerun_reasoning_delta":
        set((st) => ({
          chartJobs: {
            ...st.chartJobs,
            [ev.chart_id]: {
              ...(st.chartJobs[ev.chart_id] || { chart_id: ev.chart_id }),
              reasoning: `${st.chartJobs[ev.chart_id]?.reasoning || ""}${ev.delta || ""}`.slice(-1600),
            },
          },
        }));
        break;
      case "chart_rerun_draft_started":
        set((st) => ({ draftCharts: { ...st.draftCharts, [ev.draft_id]: {
          draft_id: ev.draft_id, page_number: ev.page_number, chart_index: 0,
          title: ev.title, chart_type: ev.chart_type, status: "draft",
          x_axis: emptyAxis(), y_axis: emptyAxis(), series: [],
        } } }));
        patchChartJob(ev.chart_id, { draft_id: ev.draft_id, phase: "constructing" });
        break;
      case "chart_rerun_draft_axes":
        patchDraft(ev.draft_id, (d) => ({ ...d, x_axis: ev.x_axis, y_axis: ev.y_axis }));
        break;
      case "chart_rerun_series_started":
        patchDraft(ev.draft_id, (d) => {
          const series = [...d.series];
          series[ev.series_index] = series[ev.series_index] || { name: ev.name, color: null, conditions: null, points: [] };
          return { ...d, series };
        });
        break;
      case "chart_rerun_points_batch":
        patchDraft(ev.draft_id, (d) => {
          const series = [...d.series];
          const current = series[ev.series_index] || { name: `Series ${ev.series_index + 1}`, color: null, conditions: null, points: [] };
          series[ev.series_index] = { ...current, points: [...current.points, ...(ev.points || [])] };
          return { ...d, series };
        });
        break;
      case "chart_rerun_complete":
        patchChartJob(ev.chart_id, { status: "completed", phase: "validated" });
        set((st) => ({
          liveCharts: st.liveCharts.map((chart) => chart.chart_id === ev.chart.chart_id ? ev.chart : chart),
          document: st.document ? {
            ...st.document,
            charts: (st.document.charts || []).map((chart) => chart.chart_id === ev.chart.chart_id ? ev.chart : chart),
          } : st.document,
        }));
        break;
      case "chart_rerun_cancelled":
        patchChartJob(ev.chart_id, { status: "cancelled", error: null });
        break;
      case "chart_rerun_failed":
        patchChartJob(ev.chart_id, { status: "failed", error: ev.message || "Re-extraction failed" });
        break;
      case "usage": {
        const prev = s.usageLive || {};
        if (ev.scope === "gate") {
          const g = prev.gate || {};
          set({ usageLive: { ...prev, gate: { model: ev.model, pricing: ev.pricing, totals: ev.totals, pages: { ...(g.pages || {}), [ev.page_number]: ev.usage } } } });
        } else {
          set({ usageLive: { ...prev, model: ev.model, pricing: ev.pricing, totals: ev.totals, pages: { ...(prev.pages || {}), [ev.page_number]: ev.usage } } });
        }
        break;
      }
      case "complete":
        set((st) => ({ status: "complete", documentJob: st.documentJob ? { ...st.documentJob, status: "completed" } : null }));
        break;
      case "error":
        set((st) => ({
          status: "error", statusMessage: ev.message,
          documentJob: st.documentJob ? { ...st.documentJob, status: "failed" } : null,
        }));
        break;
      default:
        break;
    }
  },

  setDocument: (doc) =>
    set((st) => ({
      document: doc,
      liveCharts: doc.charts || [],
      pageStates: (doc.pages || []).reduce(
        (acc, page) => ({ ...acc, [page.page_number]: { ...(st.pageStates[page.page_number] || {}), ...manifestPageState(page) } }),
        {}
      ),
    })),
  selectChart: (id) => set({ selectedChartId: id }),

  // Replace one chart (after an edit/revert/finalize) in both places.
  updateChart: (chart) =>
    set((st) => ({
      liveCharts: st.liveCharts.map((c) => (c.chart_id === chart.chart_id ? chart : c)),
      document: st.document
        ? { ...st.document, charts: (st.document.charts || []).map((c) => (c.chart_id === chart.chart_id ? chart : c)) }
        : st.document,
    })),
}));
