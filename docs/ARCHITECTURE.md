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
   │                                      investigations, graph, indicators, red_flags, x402,
   │                                      analyst, alerts, cases, sources, models, auth, admin,
   │                                      system_status, bigquery (API only; the page was removed)
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
   ├── features (app/features/*)          wallet_window.py: the same feature code live and in training
   ├── ml (app/ml/*)                      model registry, live scoring, SHAP (M12)
   ├── redflags (app/redflags/*)          authority rule packs: facts, condition language, engine
   │                                      (packs are YAML files in backend/rule_packs/)
   ├── x402 (app/x402/*)                  x402 snapshot, payment rules, Base reader, CDP client, monitor
   ├── analyst (app/analyst/*)            AI analyst: fact sheet, Ollama and Claude CLI clients,
   │                                      read-only tools, answer checks (M14, runs locally)
   ├── graph (app/graph/expand.py)        graph explorer expansion, streamed as NDJSON
   ├── alerts (app/alerts/*)              live alerts: Ethereum WebSocket; Bitcoin, Base, Solana
   │                                      and x402 polling
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
| `authority_rules` | `live_lookup`, then the authority red flags (rule packs) merged into the result | Bitcoin address |
| `random_forest`, `xgboost` | Scores a Bitcoin address live (v2+exp model, features from the provider) with SHAP | Any mainnet Bitcoin address with confirmed history |
| `x402_rules` | x402 payment and address checks against the x402 snapshot, sanctions and amount rules (mode `x402`) | Base transaction or address, Solana address |
| `fixture_review` | Runs in the browser on synthetic demo cases | demo cases |

A Bitcoin address looked up with `live_lookup` also gets, from the browser at the same time, the live
XGBoost score (`POST /api/investigations/model-score`) and the red flags (`POST /api/red-flags/bitcoin`);
both are merged into the result so the tabs, the evidence export and the AI analyst see them.

The method list the screen offers is `frontend/src/config/modes.js`; "planned" methods show greyed
out with the milestone that adds them.

## Data sources and failover

Each source is an adapter with the same interface (`app/providers/base.py`). Bitcoin uses
mempool.space then blockstream.info; Ethereum uses Etherscan, then Blockscout (with
`BLOCKSCOUT_API_KEY`), then Alchemy; Base and Solana use Alchemy only. `failover.py` sends a request to the first ready source, cools a source down after a
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
`cases`, `case_items`, `data_sources`, `app_meta`, `audit_log`, `api_usage`. The live-alert watchlist table
`alert_watch_addresses` is created on first use by `app/alerts/manual.py`. Saving runs after the
response is sent and never fails a request (`db.run_safely`).

## Sign-in and roles

Every data route needs a signed-in user (`require_viewer`). New sign-ups are viewers (read-only);
an admin promotes people to analyst (cases, watchlist, AI analyst) or admin (sources, rule packs, users,
Models page, deleting cases). The first admin is created from the command line only. Passwords are salted scrypt hashes;
session tokens are stored as SHA-256 hashes. `AUTH_REQUIRED=false` turns sign-in off for local work
(a guest viewer). Admin actions are written to `audit_log`.

## Frontend

`frontend/src/App.jsx` is a shell with hash routing (`#/cases/12`). The sidebar (`components/Sidebar.jsx`)
lists Investigate, Cases, Live alerts, Rule packs, x402 payments, Models (admins), Status, Sources and
Users (admins); there are also Overview, Account, Login and the Privacy/Terms pages. Only the Soft
(neumorphism) design is kept (`neu/neu.css`).

Investigate (`pages/InvestigatePage.jsx`, `hooks/useInvestigation.js`): a search bar, the graph, a
right column with the model risk score (`investigate/RiskPanel.jsx`, XGBoost / Random Forest switch,
score any Bitcoin address picked in the graph), the red flags (`investigate/RedFlagsPanel.jsx`) and entity
details, and the result tabs (`components/ResultTabs.jsx`): Findings, Why Flagged? (SHAP chart),
Evidence, Provenance, AI Analyst (`components/AnalystPanel.jsx`) and Evidence Assessment, with JSON
and Markdown export (`services/evidenceExport.js`).

The graph is the graph explorer (`investigate/GraphExplorer.jsx`, Sigma/WebGL, loaded lazily by
`investigate/InvestigateGraph.jsx`). It starts from the result's graph, expands the looked-up address
once, and lets the analyst expand any node (`POST /api/graph/expand`), open grouped "+N more", dust and
hub nodes, filter by time (`w3/W3TimeSlider.jsx`) and amount, and load older transactions
(`POST /api/investigations/older-transactions`, all four chains). Every expansion is merged into the
result (`mergeExpansion`), so evidence, export and the AI analyst see it. The old standalone Graph
explorer page was removed; `#/graph/<chain>/<addresses>` links from cases and alerts now open
Investigate. `components/GraphView.jsx` only shows the empty, loading and error states. Right-click
or use the node action bar to add any address or transaction to a case.

## Live alerts

`app/alerts/`: Ethereum uses one Alchemy WebSocket subscription filtered to case, watchlist and OFAC
addresses (up to 500); Bitcoin, Base and Solana poll the hand-made watchlist (`poller.py`; Solana at
most one call per 30 s, Base one check every 3 min). The x402 monitor (`app/x402/monitor.py`) polls
the riskiest x402 servers through Blockscout, or every x402 payment on Base each minute with a CDP key. Alerts are kept in memory (newest 200) and shown on the Live alerts page. Caps and costs:
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
- A new authority red-flag pack: a YAML file in `backend/rule_packs/` (copy `_TEMPLATE.yaml`; check it
  with `python -m app.redflags validate`), or upload it on the Rule packs page. No code needed. A new
  fact for packs: `app/redflags/facts.py` (`CATALOG` and `bitcoin_facts`).
- A new live-compatible model family: `FAMILIES` / `build()` in `training/model_registry.py`, its SHAP
  units in `ml/explain.py`, the family name in `routes/investigations.py` (`ModelScoreRequest`) and
  `run_investigation()`, and `MODEL_FAMILIES` in `frontend/src/services/modelScore.js`.
- A new x402 rule: `app/x402/rules.py` (add its id to `TRIGGERS` if it should raise live alerts).
- Another AI analyst model provider: a client next to `analyst/ollama.py` and `analyst/claude_cli.py`,
  its name in `PROVIDERS` (`analyst/settings.py`) and a branch in `analyst/service.py`.
