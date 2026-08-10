// Single source of truth for where the backend lives.
//
// LOCAL DEV: VITE_API_BASE is unset -> API_BASE = "" -> the app uses relative
//   paths ("/api", "/storage", "/ws") which Vite's dev proxy forwards to
//   http://localhost:8000. Nothing about local development changes.
//
// PRODUCTION (Vercel): set VITE_API_BASE = https://pdf-to-graph.onrender.com in
//   the Vercel project env vars. The app then calls that absolute URL, and the
//   WebSocket uses the matching wss:// host.
export const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");

// Build a ws:// or wss:// URL for a given path (e.g. "/ws/documents/abc").
export function wsUrl(path) {
  if (API_BASE) {
    const u = new URL(API_BASE);
    const proto = u.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${u.host}${path}`;
  }
  // Local dev: same host as the page, proxied by Vite.
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${path}`;
}
