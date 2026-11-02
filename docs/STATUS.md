# Milestone status and known gaps

As of 2 November 2026 (end of Week 3 of 8 in the project plan). Branch `dashboard-update-2026-10-30`.

## Milestones (project plan, M1-M16)

| # | Milestone | Status | Where |
| --- | --- | --- | --- |
| M1 | Backend skeleton, first live Bitcoin lookup | Done | `app/providers/bitcoin_mempool.py`, `tests/test_bitcoin_lookup.py` |
| M2 | Dashboard screen on labelled demo data | Done | `frontend/src/pages/InvestigatePage.jsx`, `data/fixtures.js` |
| M3 | Screen connected to live data | Done, and extended to Ethereum, Base and Solana | `app/services/*`, LIVE badge |
| M4 | Data inspection and dataset decision | Done: Elliptic++ labels + BigQuery-rebuilt features | `reports/elliptic_pp_data_card.md`, `reports/bigquery_training_dataset.md`, `training/FEATURE_MISMATCH.md` |
| M5 | Address activity, caching, watchlist | Done: failover cache, paging of older transactions (2 Nov), live-alert watchlist | `GET /api/bitcoin/address/{a}/transactions?after_txid=`, `app/alerts/` |
| M6 | Saved investigations | Done, in Neon Postgres instead of the planned SQLite | `app/db.py`, `GET /api/investigations/{id}` |
| M7 | Rule baseline (OFAC, scam domains) | Done, with eight more lists than planned | `app/intelligence/` |
| M8 | Interactive graph with a NetworkX measure | Done (2 Nov): Cytoscape graph, node limits, "Graph measures" method (betweenness, PageRank, degree) | `app/services/graph_measures.py` |
| M9 | Model dossiers | Done (2 Nov), plus the alert threshold re-picked on steps 50-101 | [MODEL_DOSSIERS.md](MODEL_DOSSIERS.md) |
| M10 | Feature pipeline and Random Forest | Done | `training/`, [ML_PIPELINE.md](ML_PIPELINE.md) |
| M11 | XGBoost and fair comparison | Done (2 Nov): logistic-regression baseline, one split, validation thresholds, confusion matrices, 3-seed rule | `training/model_registry.py`, `reports/model_registry.md` |
| M12 | SHAP and comparison panel | Done (2 Nov): model registry, scoring endpoint, SHAP "Why flagged?", Models page | `app/ml/`, `frontend/src/pages/ModelsPage.jsx` |
| M13 | x402 demonstration mode | Not started. Only the synthetic demo case exists; the research team's x402 records have not arrived | `modes.js` (`x402_reconcile` is "planned") |
| M14 | AI Analyst | Not started. The "Explanation" tab is a template summary built only from the findings | `components/ResultTabs.jsx` |
| M15 | Evidence export, scorecard adapter | Partly: the scorecard shows an honest "not configured"; no JSON/Markdown export yet | `ResultTabs.jsx` |
| M16 | Tests, deployment, README, demo | Partly: 116 offline backend tests, README and these docs; no deployment or demo video | `backend/tests/` |

Built beyond the plan: sign-in with viewer/analyst/admin roles and an audit log; cases; Status and
Sources pages; Bitcoin source failover; Ethereum (Etherscan, Alchemy), Base and Solana lookups;
holdings and NFTs; live alerts; five extra scam-website lists; hop features; an Elliptic2 subgraph
benchmark (`reports/elliptic2_models.md`).

## Next steps, in order

1. M13 x402 demonstration dataset and statuses (due 22 Nov). Chase the research team for the x402
   records; otherwise build a documented synthetic set with a visible label.
2. M14 AI Analyst with templates first; every sentence cites an evidence ID (due 29 Nov).
3. M15 JSON and Markdown export of a result.
4. M16 end-to-end checks per mode, deployment (or a scripted local demo) and the demo video.

## Known gaps and caveats

Models
- Live addresses cannot be scored. Hop features need each counterparty's recent transactions,
  which the live lookup does not fetch, so the model methods and the Models page only score dataset
  addresses and say so.
- Labels stop in October 2017; scores drift lower on 2018-19 data. Ransomware is barely detected
  (3.2% of Ransomwhere rows after step 49 at the recent threshold). See [ML_PIPELINE.md](ML_PIPELINE.md).
- SMOTE was not run (reasons in the dossiers). Elliptic2 models were benchmarked but not saved.
- `training/alert_queue.py` still uses the steps 1-49 threshold.

Product
- The API is not deployed; only the frontend has Vercel instructions. A hosted API would need
  `FRONTEND_ORIGIN`, the `.env` secrets and about 1 GB of RAM for the threat lists and models.
- Live alerts are kept in memory (newest 200) and are lost on restart.
- The BigQuery refresh page only estimates costs; `POST /api/bigquery/run` returns 501 by design.
- Graph measures describe only the graph shown (first page of transactions, 50-node cap).
- Ethereum has no backup source unless Alchemy is turned on (`ALCHEMY_ENABLED`, off by default
  until the free-tier budget is agreed). The Alchemy key on the original PC has Base disabled.
- Starting a new investigation while the graph is still animating can log a harmless Cytoscape
  error in the browser console.
- The frontend bundle is about 800 kB (Vite warns above 500 kB); code-splitting Cytoscape would fix it.

Data and licences
- CryptoScamDB has no licence: lookups only, never in git, reports or training.
- Tranco includes CC BY-NC inputs: internal, non-commercial use only.
- Elliptic++ states no licence: confirm use with the supervisor; never commit the CSVs.
- Datasets, models and threat lists are not in git. A new machine needs `backend/models/` copied
  across (or rebuilt) for the Models page, and the BigQuery data rebuilt for retraining.
