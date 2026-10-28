import { create } from "zustand";

export const orderCharts = (charts) => [...charts].sort((a, b) =>
  (a.page_number - b.page_number) || a.chart_id.localeCompare(b.chart_id, undefined, { numeric: true }));

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
  eventCursor: null,
  selectedChartId: null,

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
    set({ view: "home", documentId: null, document: null, liveCharts: [], logs: [], eventCursor: null, selectedChartId: null }),

  startProcessing: (documentId, name) =>
    set({
      view: "processing",
      documentId,
      document: { document_id: documentId, name, status: "uploaded" },
      liveCharts: [],
      progress: { pages_total: 0, pages_rendered: 0, pages_analyzed: 0, charts_detected: 0, charts_extracted: 0 },
      status: "uploaded",
      statusMessage: "Uploaded, starting…",
      logs: [],
      eventCursor: null,
    }),

  openWorkspace: (doc) =>
    set({
      view: "workspace",
      documentId: doc.document_id,
      document: doc,
      liveCharts: orderCharts(doc.charts || []),
      selectedChartId: (doc.charts && doc.charts[0]?.chart_id) || null,
      status: doc.status,
    }),

  handleEvent: (ev) => {
    const s = get();
    if (ev.document_id && ev.document_id !== s.documentId) return false;
    if (ev.sequence && ev.stream_id) {
      if (s.eventCursor?.stream_id === ev.stream_id && ev.sequence <= s.eventCursor.sequence) return false;
      set({ eventCursor: { stream_id: ev.stream_id, sequence: ev.sequence } });
    }
    switch (ev.type) {
      case "snapshot":
        set({ document: ev.document, liveCharts: orderCharts(ev.document.charts || []),
          progress: ev.document.progress, status: ev.document.status, statusMessage: ev.document.status_message });
        break;
      case "page_rendered": {
        const doc = s.document || {};
        const pages = [...(doc.pages || []).filter((p) => p.page_number !== ev.page_number),
          { page_number: ev.page_number, label: ev.label, kind: ev.kind, image: ev.image }]
          .sort((a, b) => a.page_number - b.page_number);
        set({ document: { ...doc, pages } });
        break;
      }
      case "status":
        set({ status: ev.status, statusMessage: ev.message });
        break;
      case "progress":
        set({ progress: ev.progress });
        break;
      case "log":
        set({ logs: [...s.logs.slice(-80), { level: ev.level, message: ev.message, t: ev.at || Date.now() }] });
        break;
      case "chart_extracted":
        set({ liveCharts: orderCharts([...s.liveCharts.filter((c) => c.chart_id !== ev.chart.chart_id), ev.chart]) });
        break;
      case "usage": {
        const document = s.document ? { ...s.document, pages: (s.document.pages || []).map((p) => p.page_number !== ev.page_number ? p :
          ev.scope === "gate" ? { ...p, gate: ev.verdict || p.gate, status: ev.verdict?.skipped ? "skipped" : p.status } :
          { ...p, usage: ev.usage, status: "analyzed" }) } : s.document;
        set({ document });
        break;
      }
      case "complete":
        set({ status: "complete" });
        break;
      case "error":
        set({ status: "error", statusMessage: ev.message });
        break;
      default:
        break;
    }
  },

  setDocument: (doc) => set({ document: doc, liveCharts: orderCharts(doc.charts || []),
    progress: doc.progress, status: doc.status, statusMessage: doc.status_message }),
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
