import test from "node:test";
import assert from "node:assert/strict";
import { useStore } from "../src/store.js";

test("replayed events do not duplicate logs or regress progress", () => {
  useStore.getState().startProcessing("doc", "Document");
  const event = { document_id: "doc", stream_id: "stream", sequence: 1, type: "log", message: "Once" };
  useStore.getState().handleEvent(event);
  assert.equal(useStore.getState().handleEvent(event), false);
  useStore.getState().handleEvent({ ...event, sequence: 3, type: "progress", progress: { pages_analyzed: 3 } });
  useStore.getState().handleEvent({ ...event, sequence: 2, type: "progress", progress: { pages_analyzed: 1 } });
  assert.equal(useStore.getState().logs.length, 1);
  assert.equal(useStore.getState().progress.pages_analyzed, 3);
});

test("charts appear immediately, then reorder 4 → 1,4 → 1,3,4", () => {
  useStore.getState().startProcessing("doc", "Document");
  const send = (n) => useStore.getState().handleEvent({ type: "chart_extracted", chart: { chart_id: `p${n}_c1`, page_number: n } });
  send(4);
  assert.deepEqual(useStore.getState().liveCharts.map((c) => c.page_number), [4]);
  send(1);
  assert.deepEqual(useStore.getState().liveCharts.map((c) => c.page_number), [1, 4]);
  send(3);
  send(4);
  assert.deepEqual(useStore.getState().liveCharts.map((c) => c.page_number), [1, 3, 4]);
});

test("old-document events cannot overwrite a new document", () => {
  useStore.getState().startProcessing("new", "New");
  assert.equal(useStore.getState().handleEvent({ document_id: "old", type: "error", message: "Old error" }), false);
  assert.equal(useStore.getState().status, "uploaded");
});

test("restart snapshots restore saved charts and progress", () => {
  useStore.getState().startProcessing("doc", "Document");
  useStore.getState().handleEvent({ type: "snapshot", document_id: "doc", document: {
    document_id: "doc", status: "complete", progress: { pages_analyzed: 5 }, charts: [
      { chart_id: "p4_c1", page_number: 4 }, { chart_id: "p1_c1", page_number: 1 }] } });
  assert.equal(useStore.getState().status, "complete");
  assert.deepEqual(useStore.getState().liveCharts.map((c) => c.page_number), [1, 4]);
  assert.equal(useStore.getState().progress.pages_analyzed, 5);
});
