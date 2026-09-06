import React, { useEffect } from "react";
import { useStore } from "./store.js";
import { api } from "./lib/api.js";
import { subscribeDocument } from "./lib/ws.js";
import Home from "./components/Home.jsx";
import Processing from "./components/Processing.jsx";
import Workspace from "./components/Workspace.jsx";
import CostSidebar from "./components/CostSidebar.jsx";

export default function App() {
  const view = useStore((s) => s.view);
  const documentId = useStore((s) => s.documentId);
  const handleEvent = useStore((s) => s.handleEvent);
  const setDocument = useStore((s) => s.setDocument);
  const goHome = useStore((s) => s.goHome);
  const theme = useStore((s) => s.theme);
  const toggleTheme = useStore((s) => s.toggleTheme);
  const costOpen = useStore((s) => s.costOpen);
  const toggleCost = useStore((s) => s.toggleCost);

  // Apply the active theme to <html data-theme="…">.
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  // Live processing subscription (active in processing view).
  useEffect(() => {
    if (view !== "processing" || !documentId) return;
    const close = subscribeDocument(documentId, async (ev) => {
      handleEvent(ev);
      if (ev.type === "complete" || ev.type === "error") {
        try {
          const doc = await api.getDocument(documentId);
          setDocument(doc);
        } catch (_) {}
      }
    });
    return close;
  }, [view, documentId, handleEvent, setDocument]);

  return (
    <>
      <header className="topbar">
        <div className="brand" onClick={goHome}>
          <div className="brand-mark">
            <svg viewBox="0 0 24 24" fill="none">
              <path d="M4 19V5m0 14h16M8 15l3-4 3 2 4-6" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <div>
            <h1>PDF Chart Studio</h1>
            <span>detect · reconstruct · edit · export</span>
          </div>
        </div>
        <div className="toolbar">
          {view !== "home" && (
            <button className="btn btn-ghost btn-sm" onClick={goHome}>
              ← Home
            </button>
          )}
          {view !== "home" && (
            <button className={`btn btn-sm ${costOpen ? "btn-primary" : ""}`} onClick={toggleCost} title="Token usage and cost for this PDF">
              $ Cost
            </button>
          )}
          <button className="theme-toggle" onClick={toggleTheme} title="Toggle light / dark">
            {theme === "dark" ? (
              <>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="4" />
                  <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
                </svg>
                Light
              </>
            ) : (
              <>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
                </svg>
                Dark
              </>
            )}
          </button>
        </div>
      </header>

      {view === "home" && <Home />}
      {view === "processing" && <Processing />}
      {view === "workspace" && <Workspace />}
      <CostSidebar />
    </>
  );
}
