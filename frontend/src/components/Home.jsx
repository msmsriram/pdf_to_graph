import React, { useCallback, useEffect, useState } from "react";
import { useDropzone } from "react-dropzone";
import { motion, AnimatePresence } from "framer-motion";
import { useStore } from "../store.js";
import { api } from "../lib/api.js";

function StatusPill({ status }) {
  const label = { uploaded: "Queued", rendering: "Rendering", analyzing: "Analyzing", complete: "Ready", error: "Failed" }[status] || status;
  return (
    <span className={`pill ${status}`}>
      <span className="dot" />
      {label}
    </span>
  );
}

export default function Home() {
  const [docs, setDocs] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [uploadPct, setUploadPct] = useState(0);
  const [error, setError] = useState(null);
  const startProcessing = useStore((s) => s.startProcessing);
  const openWorkspace = useStore((s) => s.openWorkspace);

  const refresh = useCallback(() => {
    api.listDocuments().then(setDocs).catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onDrop = useCallback(
    async (files) => {
      const file = files[0];
      if (!file) return;
      setError(null);
      setUploading(true);
      setUploadPct(0);
      try {
        const res = await api.uploadDocument(file, setUploadPct);
        startProcessing(res.document_id, res.name);
      } catch (e) {
        setError(e?.response?.data?.detail || "Upload failed");
      } finally {
        setUploading(false);
      }
    },
    [startProcessing]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "application/pdf": [".pdf"] },
    multiple: false,
  });

  const openDoc = async (id) => {
    try {
      const doc = await api.getDocument(id);
      if (doc.status === "complete" || (doc.charts && doc.charts.length)) openWorkspace(doc);
      else {
        // still processing — re-attach to the live feed
        startProcessing(doc.document_id, doc.name);
      }
    } catch (_) {}
  };

  const del = async (e, id) => {
    e.stopPropagation();
    await api.deleteDocument(id);
    refresh();
  };

  return (
    <div className="container page">
      <div className="card" {...getRootProps()} style={{ padding: 0 }}>
        <div className={`dropzone ${isDragActive ? "active" : ""}`}>
          <input {...getInputProps()} />
          <div className="drop-icon">{uploading ? "⏳" : "📄"}</div>
          {uploading ? (
            <>
              <div className="big">Uploading… {uploadPct}%</div>
              <div className="progress-track" style={{ maxWidth: 320, margin: "14px auto 0" }}>
                <div className="progress-fill" style={{ width: `${uploadPct}%` }} />
              </div>
            </>
          ) : (
            <>
              <div className="big">Drop a PDF here, or click to browse</div>
              <div className="sub">Every chart in the document will be detected, reconstructed and made editable.</div>
            </>
          )}
        </div>
      </div>
      {error && <div className="warn-banner" style={{ marginTop: 14 }}>⚠ {error}</div>}

      <div className="section-title">History</div>
      {docs.length === 0 ? (
        <div className="muted">No documents yet. Upload your first PDF above.</div>
      ) : (
        <div className="history-grid">
          <AnimatePresence>
            {docs.map((d) => (
              <motion.div
                key={d.document_id}
                layout
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.96 }}
                className="card doc-card"
                onClick={() => openDoc(d.document_id)}
              >
                <button className="btn btn-ghost btn-sm doc-del btn-danger" onClick={(e) => del(e, d.document_id)}>
                  ✕
                </button>
                <div className="doc-name">{d.name}</div>
                <StatusPill status={d.status} />
                <div className="doc-meta">
                  <span>📑 {d.page_count} pages</span>
                  <span>📊 {d.charts_count} charts</span>
                </div>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}
