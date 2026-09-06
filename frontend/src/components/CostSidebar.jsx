import React from "react";
import { useStore } from "../store.js";

// Slide-out drawer with the document's token usage and USD cost: totals, per page,
// and per chart re-extraction. Live during processing (WebSocket `usage` events),
// persisted in the manifest afterwards. Figures are the API's billed token counts
// times the model's list price (standard tier).
const usd = (v) => `$${(Number(v) || 0).toFixed(4)}`;
const int = (v) => (Number(v) || 0).toLocaleString();
const secs = (v) => `${(Number(v) || 0).toFixed(1)}s`;

export default function CostSidebar() {
  const view = useStore((s) => s.view);
  const costOpen = useStore((s) => s.costOpen);
  const toggleCost = useStore((s) => s.toggleCost);
  const document = useStore((s) => s.document);
  const usageLive = useStore((s) => s.usageLive);
  if (view === "home") return null;

  const docUsage = (document && document.usage) || null;
  const model = (usageLive && usageLive.model) || (docUsage && docUsage.model) || "—";
  const pricing = (usageLive && usageLive.pricing) || (docUsage && docUsage.pricing) || null;
  const totals = (usageLive && usageLive.totals) || (docUsage && docUsage.totals) || null;

  // Per-page rows: prefer the manifest's pages; while processing, fall back to live events.
  let rows = (document && document.pages ? document.pages : [])
    .map((p) => ({ n: p.page_number, u: (usageLive && usageLive.pages && usageLive.pages[p.page_number]) || p.usage || null }))
    .filter((r) => r.u);
  if (!rows.length && usageLive && usageLive.pages) {
    rows = Object.keys(usageLive.pages)
      .map(Number)
      .sort((a, b) => a - b)
      .map((n) => ({ n, u: usageLive.pages[n] }));
  }
  const reruns = (docUsage && docUsage.reruns) || [];

  return (
    <aside className={`cost-drawer ${costOpen ? "open" : ""}`} aria-hidden={!costOpen}>
      <div className="cost-head">
        <div>
          <div style={{ fontSize: 15, fontWeight: 650 }}>Cost &amp; tokens</div>
          <div className="muted">{document?.name || ""}</div>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={toggleCost} aria-label="Close">✕</button>
      </div>

      <div className="card" style={{ padding: 14 }}>
        <div className="muted" style={{ marginBottom: 4 }}>Total for this PDF</div>
        <div className="cost-big">{totals ? usd(totals.cost_usd) : "—"}</div>
        <div className="muted" style={{ marginTop: 2 }}>
          <span className="mono">{model}</span>
          {pricing && pricing.known
            ? ` · $${pricing.input} in · $${pricing.cached_input} cached · $${pricing.output} out / 1M tokens`
            : " · pricing unknown for this model — set PRICE_*_PER_M env vars"}
        </div>
        {totals && (
          <div className="cost-grid">
            <div><span>Input tokens</span><b>{int(totals.input_tokens)}</b></div>
            <div><span>of which cached</span><b>{int(totals.cached_tokens)}</b></div>
            <div><span>Output tokens</span><b>{int(totals.output_tokens)}</b></div>
            <div><span>of which reasoning</span><b>{int(totals.reasoning_tokens)}</b></div>
            <div><span>API calls</span><b>{int(totals.calls)}</b></div>
            <div><span>Model time</span><b>{secs(totals.seconds)}</b></div>
          </div>
        )}
        {!totals && <div className="muted" style={{ marginTop: 8 }}>No usage recorded yet — figures appear as each page is analyzed.</div>}
      </div>

      <div className="section-title">Per page</div>
      {rows.length === 0 ? (
        <div className="muted">Waiting for the first page…</div>
      ) : (
        <table className="cost-table">
          <thead>
            <tr><th>Page</th><th>In</th><th>Out</th><th>Reason.</th><th>Time</th><th>Cost</th></tr>
          </thead>
          <tbody>
            {rows.map(({ n, u }) => (
              <tr key={n}>
                <td>p{n}</td>
                <td>{int(u.input_tokens)}</td>
                <td>{int(u.output_tokens)}</td>
                <td>{int(u.reasoning_tokens)}</td>
                <td>{secs(u.seconds)}</td>
                <td><b>{usd(u.cost_usd)}</b></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {reruns.length > 0 && (
        <>
          <div className="section-title">Chart re-extractions</div>
          <table className="cost-table">
            <thead>
              <tr><th>Chart</th><th>Effort</th><th>In</th><th>Out</th><th>Cost</th></tr>
            </thead>
            <tbody>
              {reruns.map((r, i) => (
                <tr key={i}>
                  <td className="mono">{r.chart_id} v{r.version}</td>
                  <td>{r.effort}</td>
                  <td>{int(r.input_tokens)}</td>
                  <td>{int(r.output_tokens)}</td>
                  <td><b>{usd(r.cost_usd)}</b></td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <div className="muted" style={{ marginTop: 16, fontSize: 11 }}>
        Token counts are the ones the API bills for each call (images and reasoning included). Cost = tokens × list price,
        standard tier; Batch/Flex or cached pricing changes would need the PRICE_* env vars adjusted.
      </div>
    </aside>
  );
}
