import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Proxy API, static storage and the WebSocket to the FastAPI backend on :8000,
// so the frontend uses same-origin relative URLs (no CORS, ws upgrades cleanly).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/storage": { target: "http://localhost:8000", changeOrigin: true },
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
