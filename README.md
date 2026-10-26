# Crypto-Dashboard

Payments Intelligence Dashboard (student project).

- `frontend/`: React + Vite app. It currently shows synthetic demo data only.
- `backend/`: Python FastAPI app for Bitcoin lookups (via mempool.space).

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

## Deploy the frontend to Vercel

Import this repository at https://vercel.com/new and set **Root Directory** to `frontend`.
Vercel detects Vite and uses `npm run build` with output folder `dist`.
