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

Storage supports two modes. `STORAGE_BACKEND=local` preserves the filesystem
behavior below. `STORAGE_BACKEND=supabase` keeps document state (including chart
versions and usage) in Postgres and original PDFs/images, rendered pages and crops
in a private Supabase Storage bucket. The local directory is then only a cache:
existing asset URLs restore missing files and verify their SHA-256 hashes.

To configure Supabase, set these **backend-only** environment variables:

```dotenv
STORAGE_BACKEND=supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SECRET_KEY=your-backend-secret-key
SUPABASE_BUCKET=pdf-chart-studio
SUPABASE_TABLE=chart_studio_documents
STORAGE_DIR=storage
```

Run `backend/supabase_schema.sql` once in Supabase SQL Editor and create a private
bucket matching `SUPABASE_BUCKET`. Alternatively, the standalone setup script
does both and can migrate existing local documents:

```powershell
pip install "psycopg[binary]"
python scripts/setup_supabase_storage.py --import-local
```

The setup script reads `SUPABASE_POOLER_HOST`, `SUPABASE_POOLER_USER` and
`SUPABASE_DB_PASSWORD` from the ignored `backend/.env`. Database credentials are
only needed for provisioning; the running app uses HTTPS and the secret API key.
Run setup from the repository root. The scripts are additive and skip document
IDs already imported. Incomplete legacy fixtures remain viewable, but cannot
reprocess when their original sources are missing.

On Render, configure the six runtime variables above on the backend service.
Keep `OPENAI_API_KEY` configured there as well. Never set the secret key in a
frontend `VITE_` variable. GitHub repository authorization alone does not configure
these service environment variables or switch the deployed app to Supabase.

Use **one backend process/instance** for this demo. At startup, abandoned
in-progress jobs are marked interrupted; they do not automatically resume.
The homepage's **Reprocess originals** action retrieves the original PDF and/or
uploaded images, creates a separate result, and starts processing with the current
model settings. Earlier charts, costs and versions stay intact. Model calls are
billable; a working `OPENAI_API_KEY` is required. Chart-only re-extraction also
restores its crop from Supabase if the local cache is empty.

The shared application access model is unchanged: anyone with application access
can view/edit/delete documents. Buckets and database tables remain private to the
backend; the backend itself provides the shared application API.

Supabase Storage and the database are separate services: file bytes are not stored
inside SQL rows. Free-plan file limits apply to each object. Full-page PNGs can
consume more storage than their original PDFs; deleting a document removes its
remote objects as well as its database state. Keep independent backups.

Verification scripts under `scripts/` test storage hashes, PDF/image decoding,
and the actual API against an empty cache. `verify_supabase_integration.py` needs
`httpx` and uses mocked extraction responses to avoid model charges; file and
database calls are real. These verification scripts use the current local demo
fixtures. `python -m unittest discover -s backend/tests` runs isolated regression
tests without network access.

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
- Shared library, no auth. Storage can be local JSON/files or Supabase Postgres/Storage.
- Supported chart types: line, area, bar (grouped/stacked), scatter, pie.
```
