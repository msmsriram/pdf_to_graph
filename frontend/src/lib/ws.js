import { wsUrl } from "./config.js";

// Subscribe to the live processing feed for a document.
// Returns a close() function. Auto-reconnects a few times on unexpected drops.
export function subscribeDocument(documentId, onEvent, getCursor = () => null) {
  let ws = null;
  let closed = false;
  let retries = 0;
  let timer = null;

  const connect = () => {
    if (closed) return;
    const cursor = getCursor();
    const query = cursor ? `?stream=${encodeURIComponent(cursor.stream_id)}&after=${cursor.sequence}` : "";
    ws = new WebSocket(wsUrl(`/ws/documents/${documentId}${query}`));

    ws.onmessage = (msg) => {
      try {
        retries = 0;
        onEvent(JSON.parse(msg.data));
      } catch (_) {
        /* ignore malformed */
      }
    };
    ws.onclose = () => {
      if (closed) return;
      if (retries < 5) {
        retries += 1;
        timer = setTimeout(connect, 600 * retries);
      }
    };
    ws.onerror = () => ws && ws.close();
  };

  connect();
  return () => {
    closed = true;
    clearTimeout(timer);
    if (ws) ws.close();
  };
}
