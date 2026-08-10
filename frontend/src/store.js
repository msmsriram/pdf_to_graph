import { create } from "zustand";

const initialTheme =
  (typeof localStorage !== "undefined" && localStorage.getItem("pcs-theme")) || "light";

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

  goHome: () => set({ view: "home", documentId: null, document: null, liveCharts: [], logs: [], selectedChartId: null }),

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
