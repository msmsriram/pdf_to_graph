import { wsUrl } from "./config.js";

// Subscribe to the live processing feed for a document.
// Returns a close() function. Auto-reconnects a few times on unexpected drops.
export function subscribeDocument(documentId, onEvent, onConnectionChange, initialSequence = 0) {
  let ws = null;
  let closed = false;
  let retries = 0;
  let lastSequence = Number(initialSequence) || 0;

  const connect = () => {
    if (closed) return;
    onConnectionChange?.(retries ? "reconnecting" : "connecting");
    ws = new WebSocket(wsUrl(`/ws/documents/${documentId}?after=${lastSequence}`));

    ws.onopen = () => {
      retries = 0;
      onConnectionChange?.("connected");
    };

    ws.onmessage = (msg) => {
      try {
        const event = JSON.parse(msg.data);
        if (event.sequence) lastSequence = Math.max(lastSequence, Number(event.sequence) || 0);
        onEvent(event);
      } catch (_) {
        /* ignore malformed */
      }
    };
    ws.onclose = () => {
      if (closed) return;
      if (retries < 5) {
        retries += 1;
        onConnectionChange?.("reconnecting");
        setTimeout(connect, 600 * retries);
      } else {
        onConnectionChange?.("disconnected");
      }
    };
    ws.onerror = () => ws && ws.close();
  };

  connect();
  return () => {
    closed = true;
    onConnectionChange?.("closed");
    if (ws) ws.close();
  };
}
