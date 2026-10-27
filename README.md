# Crypto-Dashboard

Payments Intelligence Dashboard (student project).

- `frontend/`: React + Vite app. It currently shows synthetic demo data only.
- `backend/`: Python FastAPI app for Bitcoin lookups (via mempool.space).

## Quick start (Windows)

Double-click `run.bat` (or type `run.bat` in a terminal opened in this folder). It sets up the Python environment and frontend packages on the first run, starts the backend (http://localhost:8000) and frontend (http://localhost:5173) in two windows, and opens the dashboard. Close the two windows to stop.

## Run locally (Windows PowerShell)

Frontend:

```
cd frontend
npm install
npm run dev
```

Backend:

```
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Save live data to Neon Postgres (optional)

The backend saves every live lookup (address totals, transactions, provider health checks) and every investigation to Postgres when `DATABASE_URL` is set. Tables are created automatically on start (see `backend/app/schema.sql`). Without it, everything still works but nothing is saved.

Create `backend/.env` once (it is gitignored, so the password stays on your PC):

```
cd backend
"DATABASE_URL=$(npx neonctl connection-string --project-id crimson-snow-79733119 --pooled)" | Out-File .env -Encoding ascii
```

Check it at http://localhost:8000/api/db/health. Saved cases: `GET /api/investigations`, `GET /api/investigations/{id}`, `GET /api/bitcoin/address/{address}/history`.

## Deploy the frontend to Vercel

Import this repository at https://vercel.com/new and set **Root Directory** to `frontend`.
Vercel detects Vite and uses `npm run build` with output folder `dist`.
