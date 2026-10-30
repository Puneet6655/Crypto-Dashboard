# Crypto-Dashboard

Payments Intelligence Dashboard (student project).

- `frontend/`: React + Vite app. It currently shows synthetic demo data only.
- `backend/`: Python FastAPI app for Bitcoin lookups (mempool.space, with blockstream.info as automatic backup).

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

## Backup data sources (automatic failover)

Bitcoin lookups try mempool.space first and blockstream.info second (`BTC_PROVIDERS` in `backend/.env`). When a source answers with a rate limit (HTTP 429), an error or a timeout, the next one answers instead and the failing one gets no requests while it cools down (30 s after a rate limit, doubling to 10 min; 10 s after an error, up to 5 min). "Not found" answers are final. Recent answers are cached (30 s; confirmed transactions for a day), so repeated lookups cost no requests. Both sources use the same Esplora API and the same conversion code, so the data and the wallet features do not depend on which one answered; `python -m scripts.check_provider_parity` (from `backend/`) checks this on live addresses.

Every response still names the source that answered (`provenance.provider`), and so do saved rows and investigation evidence. `GET /api/bitcoin/providers` shows which sources are ready or cooling down, their last errors and recent fallbacks (`?check=true` also pings each one).

Blockchair was left out: it blocked keyless requests from this PC (HTTP 430). Keyed providers for other chains (Alchemy, Helius) are not added yet.

## Sanctions and ransomware lists

The backend downloads these public lists into `backend/data/raw/indicators/` (gitignored) on start, then re-checks hourly and refreshes any list older than a day:

- OFAC sanctioned addresses for Bitcoin, Ethereum-format and Solana (0xB10C's nightly extraction of OFAC's SDN list)
- Ransomwhere ransomware payment addresses (Bitcoin; mostly 2016-2021, newest submission October 2024)

Each list is saved with a `.meta.json` file (URL, download time, SHA-256, address count, when the list itself last changed). A download that fails, has the wrong shape or loses more than half its addresses is rejected and the previous copy is kept. `bitcoin_labels.csv` in the same folder lists every Bitcoin address with its source, family and dates, for training.

Every investigation checks each address in its graph for an exact match ("Why Flagged?" tab). Matches appear as findings with the list snapshot as evidence. No match only means "not in this snapshot", not "safe".

From `backend/`: `..\.venv\Scripts\python.exe -m app.intelligence refresh` (or `status`, or `check <address>`). API: `GET /api/indicators/status`, `GET /api/indicators/check/{address}`.

## Deploy the frontend to Vercel

Import this repository at https://vercel.com/new and set **Root Directory** to `frontend`.
Vercel detects Vite and uses `npm run build` with output folder `dist`.

## Accounts, roles and pages

The dashboard has a side panel with these pages: **Investigate** (the original lookup and graph), **Cases**,
**Status** (live lookup sources, the Neon database, sanction-list dates), **Sources** (acknowledgements),
**BigQuery refresh** (under development) and, for admins, **Users**.

Everyone signs in. New sign-ups are **viewers**: they can use every page but change nothing. An admin can
make someone an **analyst** (creates cases and adds addresses and transactions to them) or an **admin**
(also edits Sources, runs BigQuery estimates, deletes cases and manages users). Every change is in the
audit log on the Users page.

Create the first admin from `backend/` (the password is typed, never shown or stored in plain text):

```powershell
..\.venv\Scripts\python.exe -m app.auth create-admin you@example.com --name "Your Name"
```

Other commands: `set-role EMAIL viewer|analyst|admin`, `reset-password EMAIL`, `disable EMAIL`, `enable EMAIL`, `list`.

Passwords are stored in Neon only as salted scrypt hashes; sign-in tokens only as SHA-256 hashes. Five wrong
passwords for one email (or twenty from one address) lock sign-in for 15 minutes.

To add an address or transaction to a case, right-click it anywhere (graph, details panel, findings) or use
**Add to case**. Your own page icons go in `frontend/public/icons/` (prompts in `frontend/ICON_PROMPTS.md`).
