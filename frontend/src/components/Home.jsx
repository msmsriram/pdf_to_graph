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

const stem = (n) => String(n || "").replace(/\.[^.]+$/, "");
const kb = (b) => (b > 1024 * 1024 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);
let seq = 0;

// Home = staging area. Drop / choose / paste (Ctrl+V) a PDF and/or images, name the
// images, then Proceed. Nothing is sent until the user clicks Proceed.
export default function Home() {
  const [docs, setDocs] = useState([]);
  const [items, setItems] = useState([]); // {id, kind:'pdf'|'image', file, label, url}
  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
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

  const pdf = items.find((i) => i.kind === "pdf");
  const images = items.filter((i) => i.kind === "image");

  // Default project name: the PDF's name, else the first image's label.
  useEffect(() => {
    if (nameTouched) return;
    setName(pdf ? stem(pdf.file.name) : images.length ? images[0].label : "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pdf?.id, images.length, images[0]?.label, nameTouched]);

  const addFiles = useCallback((files) => {
    setError(null);
    setItems((prev) => {
      let next = [...prev];
      for (const f of files) {
        const n = (f.name || "").toLowerCase();
        const isPdf = f.type === "application/pdf" || n.endsWith(".pdf");
        const isImg = /^image\/(png|jpeg|webp)$/.test(f.type) || /\.(png|jpe?g|webp)$/.test(n);
        if (isPdf) {
          next = next.filter((i) => i.kind !== "pdf"); // one PDF per upload: the new one replaces
          next.push({ id: `f${++seq}`, kind: "pdf", file: f, label: f.name });
        } else if (isImg) {
          next.push({ id: `f${++seq}`, kind: "image", file: f, label: stem(f.name) || `Image ${next.length + 1}`, url: URL.createObjectURL(f) });
        } else {
          setError(`Unsupported file: ${f.name || f.type}. Use PDF, PNG, JPEG or WebP.`);
        }
      }
      return next;
    });
  }, []);

  // Ctrl+V: paste a screenshot straight from the clipboard.
  useEffect(() => {
    const onPaste = (e) => {
      const files = [];
      for (const it of e.clipboardData?.items || []) {
        if (it.kind === "file" && it.type.startsWith("image/")) {
          const blob = it.getAsFile();
          if (blob) {
            const ext = (it.type.split("/")[1] || "png").replace("jpeg", "jpg");
            files.push(new File([blob], `pasted-${new Date().toISOString().slice(11, 19).replace(/:/g, "")}.${ext}`, { type: it.type }));
          }
        }
      }
      if (files.length) {
        e.preventDefault();
        addFiles(files);
      }
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [addFiles]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop: addFiles,
    accept: { "application/pdf": [".pdf"], "image/png": [".png"], "image/jpeg": [".jpg", ".jpeg"], "image/webp": [".webp"] },
    multiple: true,
  });

  const rename = (id, label) => setItems((prev) => prev.map((i) => (i.id === id ? { ...i, label } : i)));
  const remove = (id) => setItems((prev) => prev.filter((i) => i.id !== id));
  const clear = () => {
    setItems([]);
    setName("");
    setNameTouched(false);
  };

  const canProceed = items.length > 0 && name.trim().length > 0 && !uploading && images.every((i) => i.label.trim());

  const proceed = async () => {
    if (!canProceed) return;
    setError(null);
    setUploading(true);
    setUploadPct(0);
    try {
      const res = await api.uploadBundle({ pdf: pdf?.file, images: images.map((i) => ({ file: i.file, label: i.label.trim() })), name: name.trim() }, setUploadPct);
      clear();
      startProcessing(res.document_id, res.name);
    } catch (e) {
      setError(e?.response?.data?.detail || "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const openDoc = async (id) => {
    try {
      const doc = await api.getDocument(id);
      if (doc.status === "complete" || doc.status === "error" || (doc.charts && doc.charts.length)) openWorkspace(doc);
      else startProcessing(doc.document_id, doc.name); // still processing — re-attach to the live feed
    } catch (_) {}
  };

  const del = async (e, id) => {
    e.stopPropagation();
    await api.deleteDocument(id);
    refresh();
  };

  const summary = [pdf ? "1 PDF" : null, images.length ? `${images.length} image${images.length > 1 ? "s" : ""}` : null].filter(Boolean).join(" + ");

  return (
    <div className="container page">
      <div className="card" {...getRootProps()} style={{ padding: 0 }}>
        <div className={`dropzone ${isDragActive ? "active" : ""}`}>
          <input {...getInputProps()} />
          <div className="drop-icon">📄🖼</div>
          <div className="big">Drop a PDF and/or images here, click to browse, or press <kbd>Ctrl</kbd>+<kbd>V</kbd> to paste a screenshot</div>
          <div className="sub">PDF pages are screened for charts first; images you add are extracted directly. Nothing runs until you press Proceed.</div>
        </div>
      </div>
      {error && <div className="warn-banner" style={{ marginTop: 14 }}>⚠ {error}</div>}

      {items.length > 0 && (
        <div className="card staging">
          <div className="staging-head">
            <div>
              <div className="section-title" style={{ margin: 0 }}>Ready to process</div>
              <div className="muted">{summary} → one document. Name the images so you can tell them apart later.</div>
            </div>
          </div>
          <div className="field" style={{ maxWidth: 420 }}>
            <label>Project name</label>
            <input
              className="input"
              value={name}
              placeholder="Name this upload"
              onChange={(e) => {
                setName(e.target.value);
                setNameTouched(true);
              }}
            />
          </div>
          <div className="stage-list">
            {items.map((it) => (
              <div key={it.id} className="stage-item">
                {it.kind === "pdf" ? <div className="stage-thumb pdf">PDF</div> : <img className="stage-thumb" src={it.url} alt="" />}
                <div className="stage-info">
                  {it.kind === "pdf" ? (
                    <>
                      <div className="t">{it.file.name}</div>
                      <div className="m">PDF · {kb(it.file.size)} · every page is screened, chart pages are extracted</div>
                    </>
                  ) : (
                    <>
                      <input className="input" value={it.label} placeholder="Name this image" onChange={(e) => rename(it.id, e.target.value)} />
                      <div className="m">Image · {kb(it.file.size)} · extracted directly (no screening)</div>
                    </>
                  )}
                </div>
                <button className="btn btn-ghost btn-sm btn-danger" onClick={() => remove(it.id)} title="Remove" disabled={uploading}>
                  ✕
                </button>
              </div>
            ))}
          </div>
          {uploading && (
            <div className="progress-track" style={{ marginTop: 12 }}>
              <div className="progress-fill" style={{ width: `${uploadPct}%` }} />
            </div>
          )}
          <div className="stage-actions">
            <span className="muted">{images.some((i) => !i.label.trim()) ? "Every image needs a name." : !name.trim() ? "Give the project a name." : "Ready."}</span>
            <div className="spacer" />
            <button className="btn btn-sm" onClick={clear} disabled={uploading}>
              Clear
            </button>
            <button className="btn btn-primary" onClick={proceed} disabled={!canProceed}>
              {uploading ? `Uploading… ${uploadPct}%` : "Proceed →"}
            </button>
          </div>
        </div>
      )}

      <div className="section-title">History</div>
      {docs.length === 0 ? (
        <div className="muted">No documents yet. Upload your first PDF or image above.</div>
      ) : (
        <div className="history-grid">
          <AnimatePresence>
            {docs.map((d) => {
              const srcs = d.sources || [];
              const nImg = srcs.filter((s) => s.kind === "image").length;
              const hasPdf = srcs.some((s) => s.kind === "pdf") || (!srcs.length && d.page_count > 0);
              const pdfPages = Math.max(0, (d.page_count || 0) - nImg);
              return (
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
                    {hasPdf && <span>📑 {pdfPages} pages</span>}
                    {nImg > 0 && <span>🖼 {nImg} image{nImg > 1 ? "s" : ""}</span>}
                    <span>📊 {d.charts_count} charts</span>
                    {d.pages_skipped > 0 && <span title="Pages the gate found no charts on">⏭ {d.pages_skipped} skipped</span>}
                  </div>
                  {nImg > 0 && (
                    <div className="doc-images">
                      {srcs.filter((s) => s.kind === "image").map((s, i) => (
                        <span key={i} className="chip">{s.label}</span>
                      ))}
                    </div>
                  )}
                </motion.div>
              );
            })}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}
