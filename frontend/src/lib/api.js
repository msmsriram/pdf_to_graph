import axios from "axios";
import { API_BASE } from "./config.js";

// API_BASE is "" locally (relative -> Vite proxy) and the Render URL in prod.
const http = axios.create({ baseURL: `${API_BASE}/api` });

export const api = {
  health: () => http.get("/health").then((r) => r.data),
  listDocuments: () => http.get("/documents").then((r) => r.data),
  getDocument: (id) => http.get(`/documents/${id}`).then((r) => r.data),
  deleteDocument: (id) => http.delete(`/documents/${id}`).then((r) => r.data),
  // One upload = one document: an optional PDF plus any number of named images.
  // `images`: [{ file, label }]. The backend turns images into pages after the PDF's.
  uploadBundle: ({ pdf, images = [], name = "" }, onProgress) => {
    const form = new FormData();
    if (pdf) form.append("files", pdf, pdf.name);
    images.forEach((im, i) => form.append("files", im.file, im.file.name || `image-${i + 1}.png`));
    form.append("name", name);
    form.append("image_labels", JSON.stringify(images.map((im) => im.label || "")));
    return http
      .post("/documents", form, {
        headers: { "Content-Type": "multipart/form-data" },
        onUploadProgress: (e) => onProgress && onProgress(Math.round((e.loaded / (e.total || 1)) * 100)),
      })
      .then((r) => r.data);
  },
  // "Process anyway" for a page the gate skipped; resolves with the updated document.
  analyzePage: (docId, pageNumber) => http.post(`/documents/${docId}/pages/${pageNumber}/analyze`).then((r) => r.data),
  saveVersion: (docId, chartId, spec, label) =>
    http.post(`/documents/${docId}/charts/${chartId}/versions`, { spec, label }).then((r) => r.data),
  revert: (docId, chartId) => http.post(`/documents/${docId}/charts/${chartId}/revert`).then((r) => r.data),
  setVersion: (docId, chartId, version) =>
    http.post(`/documents/${docId}/charts/${chartId}/set-version/${version}`).then((r) => r.data),
  finalize: (docId, chartId) => http.post(`/documents/${docId}/charts/${chartId}/finalize`).then((r) => r.data),
  // Re-extract one chart at a higher reasoning effort; returns the updated chart (new version appended).
  rerunChart: (docId, chartId, opts) =>
    http.post(`/documents/${docId}/charts/${chartId}/rerun`, opts || {}).then((r) => r.data),
};

// Build a static asset URL for a page image / chart crop.
export const assetUrl = (docId, rel) => `${API_BASE}/storage/${docId}/${rel}`;
