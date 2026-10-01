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
| POST | `/api/documents` | Upload one PDF and/or images, starts background processing, returns `document_id` |
| POST | `/api/documents/{id}/pages/{n}/analyze` | Force a skipped page or retry a failed page |
| GET | `/api/documents` | History list (home page) |
| GET | `/api/documents/{id}` | Full document manifest (pages, charts, versions) |
| DELETE | `/api/documents/{id}` | Delete a document |
| POST | `/api/documents/{id}/charts/{cid}/versions` | Save an edit as a new version |
| POST | `/api/documents/{id}/charts/{cid}/rerun` | Start a streamed, cancellable chart re-extraction job |
| POST | `/api/documents/{id}/jobs/{job_id}/cancel` | Stop an active document or chart job |
| POST | `/api/documents/{id}/charts/{cid}/revert` | Step back one version (undo) |
| POST | `/api/documents/{id}/charts/{cid}/set-version/{v}` | Jump to a specific version |
| POST | `/api/documents/{id}/charts/{cid}/finalize` | Mark current version as final |
| GET | `/storage/{id}/...` | Static page images & chart crops |

**WebSocket** — `/ws/documents/{id}?after=<sequence>` — the live processing feed.
It includes gate verdicts, safe Astra reasoning summaries, semantic draft-chart updates,
validated charts, per-page failures, progress and usage. Every event has a monotonic
`sequence`; the client reconnects from its last one. Events are journaled to disk, so a
late or briefly disconnected client can replay what it missed. The final validated
manifest remains authoritative over every provisional streamed value.

The default processing screen is the visual “foundry” experience. Users can switch to
the original technical pipeline/log view at any time.

Document processing and chart re-extraction are tracked jobs. Their Stop controls are
enabled only while the relevant job is active. Cancellation closes the active model
response stream and prevents a cancelled chart re-extraction from appending a version.

---

## 4. What lands on disk

```
backend/storage/
  index.json                      # history
  <document_id>/
    original.pdf
    manifest.json                 # source of truth (pages, charts, versions)
    events.jsonl                  # sequenced live-event replay journal
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
- Event replay survives a server restart, but in-flight background jobs do not yet resume
  after a process restart (a durable worker queue is outside the MVP).
- Supported chart types: line, area, bar (grouped/stacked), scatter, pie.
```
