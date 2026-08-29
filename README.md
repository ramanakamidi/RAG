# RAG Chatbot (FastAPI + React + Neon pgvector)

A full-stack RAG (Retrieval-Augmented Generation) chatbot based on [chatbot_with_rag.ipynb](../chatbot_with_rag.ipynb).

- **Backend**: FastAPI — PDF upload, text chunking, embeddings (Gemini `gemini-embedding-2`), retrieval + generation (Gemini).
- **Database**: Neon (PostgreSQL) with `pgvector` for vector search.
- **Frontend**: React (Vite) chat UI with PDF upload.

## Project structure

```
backend/   FastAPI app (main.py, requirements.txt, render.yaml)
frontend/  React + Vite app (src/App.jsx)
```

## Local development

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
cp .env.example .env            # fill in your keys
uvicorn main:app --reload
```

API runs at `http://localhost:8000` (Swagger docs at `/docs`).

Embeddings use the Gemini API (`gemini-embedding-2`, 768-dim) by default, so
there is no local model to download. Optional local mode: set
`EMBEDDING_PROVIDER=local` and `pip install sentence-transformers` (adds torch,
a few hundred MB of RAM).

### Frontend

```bash
cd frontend
npm install
npm run dev
```

App runs at `http://localhost:3000`. In dev, `/api` is proxied to
`http://localhost:8000`, so no env var is needed.

To point at a deployed backend, create `.env.local`:

```
VITE_BACKEND_URL=https://your-backend.onrender.com
```

## Deployment

### Backend → Render

Option A (Blueprint):
1. Push this repo to GitHub.
2. In Render → New → Blueprint, select the repo.
3. `backend/render.yaml` defines the web service.
4. Set `DATABASE_URL` and `GEMINI_API_KEY` in your Render service Env Vars
   (`sync: false` means you provide them manually).

Option B (Manual web service):
1. New Web Service → connect repo → Root directory: `backend`.
2. Build command: `pip install -r requirements.txt`
3. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Add env vars: `DATABASE_URL`, `GEMINI_API_KEY`.

Note: the service pins Python 3.12 via `backend/runtime.txt` (must be in the Root
Directory). Do NOT remove it — newer Python versions lack prebuilt wheels for
`psycopg2-binary` and break at import time. After changing `runtime.txt`, choose
"Clear build cache" when triggering the manual deploy.

### Frontend → Vercel

1. Import the repo in Vercel → Root directory: `frontend`.
2. Framework preset: Vite (build `npm run build`, output `dist`).
3. Environment variable: `VITE_BACKEND_URL=https://your-backend.onrender.com`.
4. Deploy.

## API endpoints

| Method | Path       | Description                                  |
| ------ | ---------- | -------------------------------------------- |
| GET    | `/health`  | Health check (incl. DB connectivity)         |
| POST   | `/upload`  | Upload a PDF → extract → embed → store       |
| POST   | `/chat`    | `{ "question": "..." }` → answer + sources   |

## Database

`pgvector` stores chunk embeddings (768-dim, Gemini `gemini-embedding-2`) in the
`documents` table. Tables are created automatically on startup
(`CREATE EXTENSION IF NOT EXISTS vector` requires superuser on Neon — Neon
provides the `vector` extension by default). If `EMBEDDING_DIMENSIONS` changes,
the `documents` table is dropped and recreated on startup (re-upload your PDFs).