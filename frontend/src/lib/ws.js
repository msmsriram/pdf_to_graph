// Subscribe to the live processing feed for a document.
// Returns a close() function. Auto-reconnects a few times on unexpected drops.
export function subscribeDocument(documentId, onEvent) {
  let ws = null;
  let closed = false;
  let retries = 0;

  const connect = () => {
    if (closed) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/documents/${documentId}`);

    ws.onmessage = (msg) => {
      try {
        onEvent(JSON.parse(msg.data));
      } catch (_) {
        /* ignore malformed */
      }
    };
    ws.onclose = () => {
      if (closed) return;
      if (retries < 5) {
        retries += 1;
        setTimeout(connect, 600 * retries);
      }
    };
    ws.onerror = () => ws && ws.close();
  };

  connect();
  return () => {
    closed = true;
    if (ws) ws.close();
  };
}
