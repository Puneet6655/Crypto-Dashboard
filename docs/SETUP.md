# Setup and configuration

Tested on Windows 11 with Python 3.15 and Node.js 22. Paths below are from the repository root.

## 1. Quick start

Double-click `run.bat`. On the first run it creates `.venv` in the repository root, installs
`backend/requirements.txt` and the frontend packages, then starts the API on http://localhost:8000
and the dashboard on http://localhost:5173 in two windows. API docs: http://localhost:8000/docs.

Without a `backend/.env`, the API has no database, so sign-in cannot work. For a first look, put
`AUTH_REQUIRED=false` in `backend/.env` (you browse as a guest viewer).

## 2. By hand

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
# Models page, model methods and training (pandas, scikit-learn, XGBoost, SHAP, BigQuery):
.venv\Scripts\python.exe -m pip install -r backend\requirements-ml.txt

cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

```powershell
cd frontend
npm install
npm run dev
```

Use the ROOT `.venv` for everything (`backend/.venv` is an old, nearly empty one). Run Python modules
from `backend/` (`python -m training.model_registry`, `python -m app.auth ...`).

If PowerShell refuses to run scripts: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## 3. Database (Neon Postgres) and the first admin

```powershell
cd backend
"DATABASE_URL=$(npx neonctl connection-string --project-id crimson-snow-79733119 --pooled)" | Out-File .env -Encoding ascii
..\.venv\Scripts\python.exe -m app.auth create-admin you@example.com --name "Your Name"
```

Tables are created on start (`app/schema.sql`). Check http://localhost:8000/api/db/health. The
password for `create-admin` is typed, never shown or stored in plain text. Other commands:
`set-role EMAIL viewer|analyst|admin`, `reset-password EMAIL`, `disable EMAIL`, `enable EMAIL`, `list`.

## 4. Environment variables (`backend/.env`, gitignored)

| Variable | Default | What it does |
| --- | --- | --- |
| `DATABASE_URL` | none | Postgres connection string. Empty: nothing saved, sign-in unavailable |
| `AUTH_REQUIRED` | `true` | `false` = no sign-in, everyone is a guest viewer (local work only) |
| `SIGNUP_ENABLED` | `true` | Whether the Sign up form creates (viewer) accounts |
| `SESSION_HOURS` | `168` | How long a sign-in lasts |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | Comma-separated origins allowed by CORS (add your Vercel URL) |
| `BTC_PROVIDERS` | `mempool,blockstream` | Bitcoin sources in failover order |
| `BTC_PROVIDER_BASE_URL` | `https://mempool.space/api` | mempool.space API |
| `BTC_ESPLORA_BASE_URL` | `https://blockstream.info/api` | Backup Esplora API |
| `BTC_NETWORK` | `mainnet` | Network label on Bitcoin entity IDs |
| `HTTP_TIMEOUT_SECONDS` | `10` | Timeout for every outside call |
| `MAX_GRAPH_NODES` | `50` | Node cap for investigation graphs |
| `ETHERSCAN_API_KEY` | none | Turns on Ethereum lookups (free key: 3 calls/s, 100,000/day) |
| `ETHERSCAN_BASE_URL`, `ETH_CHAIN_ID` | V2 API, `1` | Etherscan endpoint and chain |
| `ALCHEMY_API_KEY` or `ALCHEMY_ENDPOINT` | none | Alchemy key (Ethereum backup, Base, Solana, holdings, live alerts) |
| `ALCHEMY_ENABLED` | `false` | Alchemy stays off until this is `true`, even with a key |
| `LIVE_ALERTS_ENABLED` | `true` | Live-alert watchers (Ethereum needs Alchemy) |
| `LIVE_ALERTS_WATCH_OFAC` | `true` | Also watch OFAC Ethereum-format addresses |
| `INDICATORS_DIR` | `backend/data/raw/indicators` | Where threat lists are saved |
| `INDICATORS_AUTO_REFRESH` | `true` | Refresh lists older than `INDICATORS_MAX_AGE_HOURS` (24) hourly |
| `MODELS_DIR` | `backend/models` | Trained models, `registry.json`, `scoring_rows.parquet` |
| `XGB_DEVICE` | `cuda` | Training only: `cpu` to skip the GPU (falls back to CPU automatically) |
| `BQ_KEY_PATH` | the single `backend/data/*.json` | Training only: Google service-account key for BigQuery |

Frontend: `VITE_API_BASE_URL` (default `http://localhost:8000`), set in `frontend/.env.local` or in
Vercel's project settings.

Secrets never go in git: `.env`, `backend/data/*.json` (the Google key), `backend/data/raw/`,
`backend/data/processed/`, `backend/models/`, `*.parquet` and `*.joblib` are all in `.gitignore`.
Keys are never logged: the Etherscan key is redacted from log lines and evidence URLs.

## 5. Models (optional, for the Models page and model methods)

The trained models are not in git. Either copy `backend/models/` from the original PC, or rebuild
them (needs the training data, see [ML_PIPELINE.md](ML_PIPELINE.md)):

```powershell
cd backend
..\.venv\Scripts\python.exe -m training.model_registry
```

Without models the dashboard still works; the Models page says no models are installed and the
model methods return a clear error.

## 6. Tests

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest -q
```

All tests run offline on synthetic fixtures (no API keys, no database, no models needed). Every
test signs in as a stand-in admin (`tests/conftest.py`). `npm run build` in `frontend/` checks the
frontend compiles.

## 7. Deploying the frontend

Import the repository at https://vercel.com/new, set Root Directory to `frontend` (Vite, `npm run
build`, output `dist`) and `VITE_API_BASE_URL` to wherever the API runs. Add that Vercel URL to the
API's `FRONTEND_ORIGIN`. The API itself is not deployed anywhere yet (see [STATUS.md](STATUS.md)).
