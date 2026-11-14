# Deploying the dashboard

The frontend goes on **Vercel**. The backend goes on **Railway** as a Docker container.

## Why the backend is not on Vercel

Vercel runs Python as serverless functions. They stop between requests and scale to zero, and that breaks parts of this API:

| What the backend does | Problem on Vercel |
|---|---|
| Live alerts: an always-open WebSocket to Alchemy, plus Bitcoin and Solana pollers | Background work stops when no request is running, so alerts are missed |
| Alerts, caches and API usage counters kept in memory | Lost whenever an instance shuts down; each instance has its own copy |
| OFAC, Ransomwhere and exchange label lists refreshed in the background and saved to disk | Only `/tmp` can be written, and it is wiped on every cold start |
| Models (85 MB) plus numpy, pandas, scikit-learn, xgboost and shap | Over the 500 MB Python limit; needs the large-functions beta and makes cold starts slow |

Vercel free (Hobby) plan: functions run up to 300 s, 2 GB memory and 1 vCPU, 4.5 MB request/response bodies, one region, and personal (non-commercial) use only. That is fine for the static frontend.

Railway keeps one container running all the time, so everything works the same as on your PC. The Hobby plan is about $5 a month, which includes $5 of usage.

## What gets uploaded where

| Piece | Where | How |
|---|---|---|
| `frontend/` | Vercel | Vercel builds it from GitHub (`npm run build`) and serves `dist/` as static files |
| `backend/app/` code | Railway | Railway builds `backend/Dockerfile` from GitHub. `.dockerignore` keeps `.env`, `data/`, `training/` and `tests/` out |
| Models | Git (private repo), then baked into the image | The 10 files the API loads (79 MB) are committed in `backend/models/`. The other model files stay gitignored |
| Database | Neon (already in the cloud) | Nothing to move. The same `DATABASE_URL` works from Railway |
| BigQuery files in `backend/data/` (25 GB) | Nowhere | Training only. The BigQuery page shows no preview on the server |
| API keys | Railway and Vercel settings | Typed into each dashboard. Never in git |

### How the models get to the server

Only the files the API loads are committed: `registry.json`, `scoring_rows.parquet`, `known_labels.parquet` and the seven `*__v2_exp*` / `xgboost__alert_hops` models. `.gitignore` lists them one by one, and every other file in `backend/models/` stays ignored. The old `random_forest.joblib` (150 MB) and the isolation forest are training-only. The Dockerfile copies `backend/models/` into the image, and the API loads the models from there on start.

After retraining, commit the updated files. If a new model file name appears in `registry.json`, add it to both `.gitignore` and `backend/.dockerignore`. Each retrain adds about 80 MB to the repo history. GitHub rejects single files over 100 MB.

**Fallback (no models in git):** on start, the container runs `python -m app.ml.fetch_models`. If `models/registry.json` is missing and `MODELS_URL` is set, it downloads a zip of the models from that URL. `MODELS_TOKEN`, if set, is sent as a Bearer token, for example to reach a private GitHub Release asset's API URL. If the download fails, the API still starts without model scores. To build that zip, run this from the repo root. It writes the gitignored `models-deploy.zip`:

```bash
.venv/Scripts/python -c "import zipfile,pathlib,re;m=pathlib.Path('backend/models');fs=['registry.json','scoring_rows.parquet','known_labels.parquet']+sorted(set(re.findall(r'\"file\": \"([^\"]+)\"',(m/'registry.json').read_text())));z=zipfile.ZipFile('models-deploy.zip','w',zipfile.ZIP_DEFLATED);[z.write(m/f,f) for f in fs];z.close();print(len(fs),'files')"
```

## Step 1: push the branch

The deploy files and slim models are on `dashboard-update-2026-10-30`. Railway and Vercel build from GitHub, so push before deploying.

## Step 2: backend on Railway

1. Sign in at railway.com with GitHub and choose **New Project → Deploy from GitHub repo → Crypto-Dashboard**.
2. In the service's **Settings**:
   - **Root Directory**: `backend`. Railway finds the Dockerfile.
   - **Branch**: the branch you deploy, e.g. `dashboard-update-2026-10-30`.
   - **Region**: Southeast Asia (Singapore), closest to the Neon database in Sydney.
   - **Networking → Generate Domain**. This gives a URL like `https://crypto-dashboard-production.up.railway.app`.
3. In **Variables**, use **Raw Editor** and paste the values from your `backend/.env`, plus the new ones:

```
DATABASE_URL=...            # from backend/.env (Neon)
ETHERSCAN_API_KEY=...
BLOCKSCOUT_API_KEY=...      # Ethereum backup after Etherscan (free key, dev.blockscout.com)
ALCHEMY_API_KEY=...
ALCHEMY_ENDPOINT=...
ALCHEMY_ENABLED=true
DUNE_API_KEY=...
FRONTEND_ORIGIN=https://<your-vercel-app>.vercel.app
AUTH_REQUIRED=true
SIGNUP_ENABLED=false
# Shown on the privacy policy and terms (legal pages are a template: have them checked before opening sign-up)
LEGAL_OPERATOR_NAME=...      # who runs the dashboard (the data controller)
LEGAL_CONTACT_EMAIL=...      # where privacy questions and data requests go
LEGAL_JURISDICTION=...       # e.g. Queensland, Australia
AUDIT_RETENTION_DAYS=365     # optional, audit log kept this long
INACTIVE_ACCOUNT_DAYS=730    # optional, accounts unused this long are deleted (0 = off)
```

   Railway sets `PORT` by itself. After the first Vercel deploy, put the real Vercel URL in `FRONTEND_ORIGIN`. You can list several origins, separated by commas.
4. Deploy. In the logs, look for `Models already in /app/models` and `Uvicorn running`. Then open `https://<railway-domain>/api/health`.

## Step 3: frontend on Vercel

1. Sign in at vercel.com with GitHub and choose **Add New → Project → import Crypto-Dashboard**.
2. **Root Directory**: `frontend`. Framework is detected as Vite, and `frontend/vercel.json` sets the build.
3. Under **Environment Variables**, add:
   - `VITE_API_BASE_URL` = `https://<railway-domain>`, with no trailing slash, for Production (and Preview if you want it).
   - `VITE_LOGIN_DESIGN` = `web3` to show the animated Web3 design (login, Home, glass pages). Leave it out for the original design. Either way, `?classic` in the URL shows the original and `?web3` shows the new one.
4. Deploy. Vite bakes `VITE_API_BASE_URL` and `VITE_LOGIN_DESIGN` into the build, so **redeploy after changing it**: Deployments → ⋯ → Redeploy.
5. Copy the Vercel URL into Railway's `FRONTEND_ORIGIN` (Step 2.3). Railway redeploys on its own.

Every push to the branch then redeploys both.

### Setting env vars with the CLI instead (optional)

```bash
npx vercel link --cwd frontend
```

```bash
npx vercel env add VITE_API_BASE_URL production --cwd frontend
```

## Checks after deploy

- `https://<railway-domain>/api/health` returns `{"status":"ok"}`.
- Sign in on the Vercel URL. A CORS error in the browser console means `FRONTEND_ORIGIN` does not match the Vercel URL exactly.
- The Status page shows the database connected, and the Models page lists the seven models.
- Live alerts: the Alerts page shows the watcher connected.

## Keeping keys safe

- `backend/.env` stays gitignored and is kept out of the image by `.dockerignore`.
- Anything starting with `VITE_` ends up in public JavaScript. Only put the API URL there, never a key.
- If a key ever lands in git, rotate it at the provider. Deleting the commit is not enough.
