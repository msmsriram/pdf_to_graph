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
    set({ view: "home", documentId: null, document: null, liveCharts: [], logs: [], selectedChartId: null, usageLive: null, costOpen: false }),

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
      usageLive: null,
    }),

  openWorkspace: (doc) =>
    set({
      view: "workspace",
      documentId: doc.document_id,
      document: doc,
      liveCharts: doc.charts || [],
      selectedChartId: (doc.charts && doc.charts[0]?.chart_id) || null,
      status: doc.status,
    }),

  handleEvent: (ev) => {
    const s = get();
    switch (ev.type) {
      case "status":
        set({ status: ev.status, statusMessage: ev.message });
        break;
      case "progress":
        set({ progress: ev.progress });
        break;
      case "log":
        set({ logs: [...s.logs.slice(-80), { level: ev.level, message: ev.message, t: Date.now() }] });
        break;
      case "chart_extracted":
        set({ liveCharts: [...s.liveCharts.filter((c) => c.chart_id !== ev.chart.chart_id), ev.chart] });
        break;
      case "usage":
        set({
          usageLive: {
            model: ev.model,
            pricing: ev.pricing,
            totals: ev.totals,
            pages: { ...((s.usageLive && s.usageLive.pages) || {}), [ev.page_number]: ev.usage },
          },
        });
        break;
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

  setDocument: (doc) => set({ document: doc, liveCharts: doc.charts || [] }),
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
