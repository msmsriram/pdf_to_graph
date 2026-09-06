# PDF Chart Studio

Upload a PDF → every chart in it is detected, reconstructed as an **editable** chart,
and rendered in a workspace where you can change data/colors/axes, keep an immutable
original, step back through versions, finalize, and export (PNG/JPEG/SVG/CSV/JSON).

Model: **gpt-6-astra** (OpenAI Responses API, direct — not Bedrock), with structured
outputs and rolling page-to-page continuity.

---

## 1. Backend (FastAPI)

```bash
cd backend
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# configure your key
copy .env.example .env      # then edit .env and paste your OPENAI_API_KEY

# run (http://localhost:8000)
uvicorn app.main:app --reload --port 8000
```

Health check: open http://localhost:8000/api/health → should show `{"ok":true,"model":"gpt-6-astra","has_key":true}`.

## 2. Frontend (React + Vite)

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173
```

The frontend proxies `/api`, `/storage`, and `/ws` to the backend on port 8000
(see `vite.config.js`), so start the backend first.

---

## 3. How the two connect (WebSocket vs REST)

**REST** — request/response actions:

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/documents` | Upload a PDF, starts background processing, returns `document_id` |
| GET | `/api/documents` | History list (home page) |
| GET | `/api/documents/{id}` | Full document manifest (pages, charts, versions) |
| DELETE | `/api/documents/{id}` | Delete a document |
| POST | `/api/documents/{id}/charts/{cid}/versions` | Save an edit as a new version |
| POST | `/api/documents/{id}/charts/{cid}/revert` | Step back one version (undo) |
| POST | `/api/documents/{id}/charts/{cid}/set-version/{v}` | Jump to a specific version |
| POST | `/api/documents/{id}/charts/{cid}/finalize` | Mark current version as final |
| GET | `/storage/{id}/...` | Static page images & chart crops |

**WebSocket** — `/ws/documents/{id}` — the live processing feed only. Event types:
`status`, `progress`, `page_rendered`, `chart_detected`, `chart_extracted`,
`page_analyzed`, `log`, `complete`, `error`. A replay buffer means a client that
connects late still gets every earlier event, then the live stream.

---

## 4. What lands on disk

```
backend/storage/
  index.json                      # history
  <document_id>/
    original.pdf
    manifest.json                 # source of truth (pages, charts, versions)
    pages/page_001.png ...
    charts/p001_c01.png ...       # cropped original chart regions
    page_001.txt ...              # human-readable XML artifact (<summary>/<charts>)
```

---

## 5. Tunables (`backend/.env`)

- `OPENAI_MODEL` — default `gpt-6-astra`. **If the API rejects the id, change only this.**
- `REASONING_EFFORT` — `high` (accuracy) down to `low` (cheaper/faster).
- `IMAGE_DETAIL` — `original` best for dense charts; `low` is cheaper.
- `RENDER_DPI` — 150–200 sweet spot.
- `MAX_PAGES` — safety cap so a huge PDF can't blow up cost.

---

## 6. MVP scope / honest limits

- Chart **values are gpt-6-astra estimates** unless printed on the source. The UI shows
  the original crop side-by-side and per-field confidence so you can verify/correct.
- Chart detection + data extraction are done by the model in one pass (the deterministic
  pixel-tracing digitizer is a planned Phase-2 accuracy upgrade).
- Single-user, local, no auth. Storage is JSON files on disk.
- Supported chart types: line, area, bar (grouped/stacked), scatter, pie.
```
