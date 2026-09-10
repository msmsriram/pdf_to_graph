import React from "react";
import { useStore } from "../store.js";

// Slide-out drawer with the document's token usage and USD cost: grand total, the
// extraction model (per page + per chart re-extraction) and the cheap gate model
// (per page verdict) shown separately so the gate's saving is visible. Live during
// processing (WebSocket `usage` events), persisted in the manifest afterwards.
// Figures are the API's billed token counts × the model's list price (standard tier).
const usd = (v) => `$${(Number(v) || 0).toFixed(4)}`;
const int = (v) => (Number(v) || 0).toLocaleString();
const secs = (v) => `${(Number(v) || 0).toFixed(1)}s`;
const rate = (p) => (p && p.known ? `$${p.input} in · $${p.cached_input} cached · $${p.output} out / 1M` : "pricing not set (set PRICING_JSON)");

export default function CostSidebar() {
  const view = useStore((s) => s.view);
  const costOpen = useStore((s) => s.costOpen);
  const toggleCost = useStore((s) => s.toggleCost);
  const document = useStore((s) => s.document);
  const usageLive = useStore((s) => s.usageLive);
  if (view === "home") return null;

  const docUsage = (document && document.usage) || null;
  const live = usageLive || {};
  const model = live.model || (docUsage && docUsage.model) || "—";
  const pricing = live.pricing || (docUsage && docUsage.pricing) || null;
  const totals = live.totals || (docUsage && docUsage.totals) || null;
  const gate = live.gate || (docUsage && docUsage.gate) || null;
  const gateTotals = gate && gate.totals;
  const grand = (totals ? Number(totals.cost_usd) || 0 : 0) + (gateTotals ? Number(gateTotals.cost_usd) || 0 : 0);
  const anyUnknown = (pricing && !pricing.known) || (gate && gate.pricing && !gate.pricing.known);

  // Per-page rows: the manifest's pages, overlaid with live events while processing.
  const pages = (document && document.pages) || [];
  let rows = pages.map((p) => ({
    n: p.page_number,
    label: p.label || `Page ${p.page_number}`,
    status: p.status,
    u: (live.pages && live.pages[p.page_number]) || p.usage || null,
    g: (live.gate && live.gate.pages && live.gate.pages[p.page_number]) || (p.gate && p.gate.usage) || null,
    verdict: p.gate || null,
  }));
  if (!rows.length) {
    const ns = new Set([...Object.keys(live.pages || {}), ...Object.keys((live.gate && live.gate.pages) || {})].map(Number));
    rows = [...ns].sort((a, b) => a - b).map((n) => ({ n, label: `Page ${n}`, u: live.pages && live.pages[n], g: live.gate && live.gate.pages && live.gate.pages[n] }));
  }
  rows = rows.filter((r) => r.u || r.g);
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
        <div className="muted" style={{ marginBottom: 4 }}>Total for this document</div>
        <div className="cost-big">{totals || gateTotals ? usd(grand) : "—"}</div>
        {anyUnknown && <div className="muted" style={{ color: "var(--warn)" }}>One model has no price set; its tokens are counted but cost as $0.</div>}
        {!totals && !gateTotals && <div className="muted" style={{ marginTop: 8 }}>No usage recorded yet — figures appear as each page is processed.</div>}

        {totals && (
          <>
            <div className="cost-sub">
              <span>Extraction · <span className="mono">{model}</span></span>
              <b>{usd(totals.cost_usd)}</b>
            </div>
            <div className="muted" style={{ fontSize: 11 }}>{rate(pricing)}</div>
            <div className="cost-grid">
              <div><span>Input tokens</span><b>{int(totals.input_tokens)}</b></div>
              <div><span>of which cached</span><b>{int(totals.cached_tokens)}</b></div>
              <div><span>Output tokens</span><b>{int(totals.output_tokens)}</b></div>
              <div><span>of which reasoning</span><b>{int(totals.reasoning_tokens)}</b></div>
              <div><span>API calls</span><b>{int(totals.calls)}</b></div>
              <div><span>Model time</span><b>{secs(totals.seconds)}</b></div>
            </div>
          </>
        )}
        {gateTotals && (
          <>
            <div className="cost-sub" style={{ marginTop: 12 }}>
              <span>Chart gate · <span className="mono">{gate.model}</span></span>
              <b>{usd(gateTotals.cost_usd)}</b>
            </div>
            <div className="muted" style={{ fontSize: 11 }}>{rate(gate.pricing)}</div>
            <div className="cost-grid">
              <div><span>Input tokens</span><b>{int(gateTotals.input_tokens)}</b></div>
              <div><span>Output tokens</span><b>{int(gateTotals.output_tokens)}</b></div>
              <div><span>Pages screened</span><b>{int(gateTotals.calls)}</b></div>
              <div><span>Pages skipped</span><b>{int(rows.filter((r) => r.verdict && r.verdict.skipped).length)}</b></div>
            </div>
          </>
        )}
      </div>

      <div className="section-title">Per page</div>
      {rows.length === 0 ? (
        <div className="muted">Waiting for the first page…</div>
      ) : (
        <table className="cost-table">
          <thead>
            <tr><th>Page</th><th>Gate</th><th>In</th><th>Out</th><th>Reason.</th><th>Time</th><th>Cost</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const v = r.verdict;
              const gateCell = !r.g ? "—" : v && v.skipped ? "skip" : v && v.overridden ? "forced" : v ? `${v.chart_count} ✓` : "✓";
              const gateTitle = v ? `${v.reason || ""} (P(charts) ${Math.round((v.p_charts ?? 0) * 100)}%)` : "";
              const cost = (r.u ? Number(r.u.cost_usd) || 0 : 0) + (r.g ? Number(r.g.cost_usd) || 0 : 0);
              return (
                <tr key={r.n} className={v && v.skipped ? "muted" : ""}>
                  <td title={r.label}>{r.label.length > 14 ? `${r.label.slice(0, 13)}…` : r.label}</td>
                  <td title={gateTitle}>{gateCell}</td>
                  <td>{int((r.u ? r.u.input_tokens : 0) + (r.g ? r.g.input_tokens : 0))}</td>
                  <td>{int((r.u ? r.u.output_tokens : 0) + (r.g ? r.g.output_tokens : 0))}</td>
                  <td>{int(r.u ? r.u.reasoning_tokens : 0)}</td>
                  <td>{secs((r.u ? r.u.seconds : 0) + (r.g ? r.g.seconds : 0))}</td>
                  <td><b>{usd(cost)}</b></td>
                </tr>
              );
            })}
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
        standard tier. Per-model prices come from the built-in table or the PRICING_JSON env var.
      </div>
    </aside>
  );
}
