# Milestone status and known gaps

As of 25 November 2026 (Week 6 of 8 in the project plan). Branch `main`.

## Milestones (project plan, M1-M16)

| # | Milestone | Status | Where |
| --- | --- | --- | --- |
| M1 | Backend skeleton, first live Bitcoin lookup | Done | `app/providers/bitcoin_mempool.py`, `tests/test_bitcoin_lookup.py` |
| M2 | Dashboard screen on labelled demo data | Done | `frontend/src/pages/InvestigatePage.jsx`, `data/fixtures.js` |
| M3 | Screen connected to live data | Done, and extended to Ethereum, Base and Solana | `app/services/*` |
| M4 | Data inspection and dataset decision | Done: Elliptic++ labels + BigQuery-rebuilt features | `training/FEATURE_MISMATCH.md`, offline reports |
| M5 | Address activity, caching, watchlist | Done: failover cache, paging, "Load older transactions" on all four chains, live-alert watchlist | `POST /api/investigations/older-transactions`, `app/alerts/` |
| M6 | Saved investigations | Done, in Neon Postgres instead of the planned SQLite | `app/db.py`, `GET /api/investigations/{id}` |
| M7 | Rule baseline (OFAC, scam domains) | Done, with ten lists | `app/intelligence/` |
| M8 | Interactive graph with a NetworkX measure | Done. The graph explorer (Sigma/WebGL, thousands of nodes, expand, group, time and amount filters) is now the Investigate graph | `frontend/src/investigate/GraphExplorer.jsx`, `app/graph/expand.py`, `app/services/graph_measures.py` |
| M9 | Model dossiers | Done | [MODEL_DOSSIERS.md](MODEL_DOSSIERS.md) |
| M10 | Feature pipeline and Random Forest | Done | `training/`, [ML_PIPELINE.md](ML_PIPELINE.md) |
| M11 | XGBoost and fair comparison | Done | `training/model_registry.py` |
| M12 | SHAP and comparison panel | Done, plus live scoring of every Bitcoin lookup (XGBoost, Random Forest switch) and 2026 thresholds | `app/ml/`, `investigate/RiskPanel.jsx`, `pages/ModelsPage.jsx` |
| M13 | x402 mode | Done in a changed form: x402 payment rules, x402 payments page and live monitor on real Base data (the research team's records did not arrive). `x402_reconcile` is still "planned" | `app/x402/`, `pages/X402Page.jsx` |
| M14 | AI Analyst | Done, runs locally: Ollama (qwen3:8b) by default, optional Claude CLI; answer checks against the case facts. Not online by the supervisor's decision | `app/analyst/`, `components/AnalystPanel.jsx` |
| M15 | Evidence export, scorecard adapter | Export done (JSON and Markdown). The scorecard adapter waits on the research team; "not configured" is shown | `services/evidenceExport.js` |
| M16 | Tests, deployment, README, demo | In progress: 253 offline backend tests and 5 frontend tests; deployed on Vercel + Railway; docs done. Demo video and final report left | `backend/tests/`, [DEPLOY.md](DEPLOY.md) |

Built beyond the plan: sign-in with viewer/analyst/admin roles and an audit log; cases; Status and
Sources pages; API usage metering; privacy features (account deletion, data export, consent,
privacy/terms pages); Bitcoin source failover; Ethereum (Etherscan, Blockscout, Alchemy), Base and
Solana; holdings and NFTs; live alerts on four chains plus x402; exchange labels; hop features; an
Elliptic2 benchmark; authority red-flag rule packs (FATF, FinCEN, AUSTRAC, OFAC); the Soft design.

## Next steps, in order

1. Set `ANALYST_ENABLED=false` on Railway: the AI analyst runs on the laptop only (see [DEPLOY.md](DEPLOY.md)).
2. Security fixes from the 14 Nov audit: confirm `SIGNUP_ENABLED=false` and `FRONTEND_ORIGIN` on
   Railway, per-user rate limits on lookup/graph/score/analyst routes, security headers in
   `frontend/vercel.json`, trusted proxy IP, hide `/docs` in production, shorter sessions; upgrade
   `cryptography` 46.0.3 and `PyJWT` 2.10.1 (pip-audit lists published advisories; used only for CDP signing).
3. One end-to-end check per mode on the live site, then code freeze.
4. Demo video, final report and presentation (13 Dec).

## Known gaps and caveats

Models
- Bitcoin only. Live addresses are scored with the v2+exp models only (no hop features: those need
  each counterparty's history). There are no 2026 labels, so live and recent scores have no measured
  accuracy. Labels stop in October 2017; ransomware is barely detected.
- SMOTE was not run (reasons in the dossiers). Elliptic2 models were benchmarked but not saved.
- `training/alert_queue.py` still uses the steps 1-49 threshold.

Red flags and AI analyst
- Rule packs run on Bitcoin facts only. `usd_near_reporting_threshold_count` needs a price feed and
  always shows "not checked". Red flags run before graph expansion, so list counts can lag the graph.
- The local model (qwen3:8b) can still reverse a payment direction on big expanded graphs. The answer
  check catches invented identifiers and amounts, not wrong reasoning. Claude (CLI) is more accurate
  but sends the case facts to Anthropic and ignores length limits.

Product
- Live alerts are kept in memory (newest 200) and are lost on restart. The graph explorer's daily
  Alchemy allowance resets when the backend restarts.
- Graph measures describe only the graph shown.
- The BigQuery page was removed; `/api/bigquery` and the training scripts remain.
- Rule packs uploaded on the page live on the server's disk (lost on a Railway redeploy).
- The frontend main bundle is 929 kB (Vite warns above 500 kB); more code-splitting would fix it.
- 8 privacy tests need a throwaway Postgres (`TEST_DATABASE_URL`) and are skipped by default.

Data and licences
- CryptoScamDB has no licence: lookups only, never in git, reports or training.
- Tranco includes CC BY-NC inputs: internal, non-commercial use only.
- Elliptic++ states no licence: confirm use with the supervisor; never commit the CSVs.
- Etherscan data must never train, tune or benchmark a model (or fine-tune the AI analyst).
- Spellbook exchange labels are BSL 1.1 until March 2027: fine for own use, not a public data platform.
- Datasets and most model files are not in git; the slim model set the API needs (79 MB) is.
