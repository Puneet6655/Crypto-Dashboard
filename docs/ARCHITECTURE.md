# Architecture

One React app talks to one FastAPI process. The API calls public blockchain APIs live, checks
addresses and websites against downloaded threat lists, saves what it fetched to Postgres (Neon),
and serves offline-benchmark models trained from BigQuery data. Training runs separately, from
the command line, and never inside the API.

```
 Browser (React + Vite, frontend/)
   │  fetch, Bearer token (sign-in)
   ▼
 FastAPI (backend/app/main.py)
   ├── routes (app/api/routes/*)          one file per area: bitcoin, ethereum/base, solana,
   │                                      investigations, indicators, alerts, cases, sources,
   │                                      models, auth, admin, status, bigquery
   ├── services (app/services/*)          run one investigation: fetch, build the graph, findings
   │     investigation_service.py         Bitcoin + dispatch to the other methods
   │     ethereum_investigation.py        Ethereum and Base
   │     solana_investigation.py          Solana
   │     rules_investigation.py           list checks only (websites, Bitcoin addresses)
   │     graph_measures.py                NetworkX measures on a finished graph (M8)
   ├── providers (app/providers/*)        one adapter per data source, behind failover
   │     failover.py                      tries sources in order, cools down failing ones, caches
   ├── intelligence (app/intelligence/*)  OFAC, Ransomwhere and scam-website lists: download,
   │                                      check, cache, exact matching
   ├── ml (app/ml/*)                      model registry, scoring, SHAP (M12, offline benchmark)
   ├── alerts (app/alerts/*)              live alerts: Ethereum WebSocket, Solana/Bitcoin polling
   ├── auth, workspace (app/auth, app/workspace)   accounts, roles, cases, sources, audit log
   └── db.py, store.py, schema.sql        Postgres pool; every live lookup and investigation saved
         │
         ▼
 Neon Postgres (optional: without DATABASE_URL nothing is saved, and since accounts live there,
               run with AUTH_REQUIRED=false)

 Training (backend/training/*, run by hand)
   BigQuery public Bitcoin data ──► wallet features ──► training_dataset.parquet
   transaction store ──► hop features ──► model_registry.py ──► backend/models/ (gitignored)
```

## The request/response contract

Every investigation, whatever the chain or method, returns one shape:
`app/schemas/investigation.py` `InvestigationResult`. It holds findings (each with evidence IDs),
indicators (rule results, matched / no match / not run), a graph (nodes, edges, limits), evidence
(source, URL, retrieval time, method version), source timestamps, limitations and uncertainty.
Model methods add `explanation` (score, threshold, SHAP). The React screen renders any result from
this shape, so a new method needs no new screen code unless it adds a new view.

`data_mode` says where the data came from: `live` (fetched now), `recorded` (the offline benchmark
dataset) or `synthetic` (demo fixtures in `frontend/src/data/fixtures.js`).

## Methods

| Method id | What it does | Inputs |
| --- | --- | --- |
| `live_lookup` | Fetches the address or transaction, draws the graph, screens every address against the lists | Bitcoin, Ethereum, Base, Solana address or transaction |
| `rules_v1` | List checks only, no transaction fetch | website or Bitcoin address |
| `graph_measures` | `live_lookup`, then betweenness, PageRank and degree on the graph (NetworkX) | as `live_lookup` |
| `random_forest`, `xgboost` | Scores a Bitcoin address from the benchmark dataset with SHAP | Bitcoin address in `models/scoring_rows.parquet` |
| `fixture_review` | Runs in the browser on synthetic demo cases | demo cases |

The method list the screen offers is `frontend/src/config/modes.js`; "planned" methods show greyed
out with the milestone that adds them.

## Data sources and failover

Each source is an adapter with the same interface (`app/providers/base.py`). Bitcoin uses
mempool.space then blockstream.info; Ethereum uses Etherscan then Alchemy; Base and Solana use
Alchemy only. `failover.py` sends a request to the first ready source, cools a source down after a
rate limit (30 s doubling to 10 min) or error (10 s to 5 min), treats "not found" as final, and
caches answers (30 s; confirmed transactions a day). Every response names the source that answered
(`provenance.provider`). Details: [DATA_SOURCES.md](DATA_SOURCES.md).

## Threat lists

`app/intelligence/sources.py` declares every list (URL, parser, licence, whether it may be used for
training). `snapshots.py` downloads into `backend/data/raw/indicators/` with a `.meta.json` per list,
rejects a download that fails to parse or loses more than half its records, and keeps a parsed cache
in `cache/<id>.pickle` keyed by the file's SHA-256, so a restart loads the lists in about 3 s instead
of 12 s. `index.py` holds them in memory for exact matching (about 120 MB). The API refreshes lists
older than a day, checking hourly (`refresher.py`).

## Database

`app/schema.sql` creates the tables on start: `address_snapshots`, `transactions`,
`transaction_flows`, `investigations`, `provider_health_checks`, `app_users`, `app_sessions`,
`cases`, `case_items`, `data_sources`, `app_meta`, `audit_log`. The live-alert watchlist table
`alert_watch_addresses` is created on first use by `app/alerts/manual.py`. Saving runs after the
response is sent and never fails a request (`db.run_safely`).

## Sign-in and roles

Every data route needs a signed-in user (`require_viewer`). New sign-ups are viewers (read-only);
an admin promotes people to analyst (cases, watchlist) or admin (sources, BigQuery, users, deleting
cases). The first admin is created from the command line only. Passwords are salted scrypt hashes;
session tokens are stored as SHA-256 hashes. `AUTH_REQUIRED=false` turns sign-in off for local work
(a guest viewer). Admin actions are written to `audit_log`.

## Frontend

`frontend/src/App.jsx` is a shell with hash routing (`#/cases/12`). Pages in `src/pages/`:
Investigate, Cases, Live alerts, Models, Status, Sources, BigQuery refresh, Users, Account, Login.
`src/components/GraphCanvas.jsx` draws the Cytoscape graph; `config/graphStyle.js` is the single
place for node colours, shapes and sizes (nodes grow with betweenness when graph measures ran).
`ResultTabs.jsx` holds the six result views; "Why Flagged?" shows rule results and, for model
methods, the SHAP chart (`ShapChart.jsx`). Right-click any address or transaction to add it to a case.

## Live alerts

`app/alerts/`: Ethereum uses one Alchemy WebSocket subscription filtered to case, watchlist and OFAC
addresses (up to 500); Solana and Bitcoin poll the hand-made watchlist (Solana at most one call per
30 s). Alerts are kept in memory (newest 200) and shown on the Live alerts page. Caps and costs:
[DATA_SOURCES.md](DATA_SOURCES.md#alchemy).

## Where to extend

- A new chain: a provider adapter behind `failover.py`, an investigation service returning
  `InvestigationResult`, a screening entry in `intelligence/screening.py` `CHECKS`, input types in
  `schemas/investigation.py` and `frontend/src/config/modes.js`.
- A new threat list: one `IndicatorSource` in `intelligence/sources.py` (say whether it may be used
  for training), a parser, and a row on the Sources page.
- A new model: add it to `training/model_registry.py` `FAMILIES` / `build()`, rerun it, and the
  Models page and `/api/models` pick it up from `registry.json`.
- A new analysis on a finished graph: follow `services/graph_measures.py` (takes a result, returns
  a copy with extra node details, findings and one evidence item).
