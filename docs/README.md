# Handover

Start here if you are new to the project. If `HANDOVER_GUIDE.md` is in this folder (it is kept offline,
not in git), read it first. The owner keeps a fuller handover package offline
(handover guide, developer guide for plugging in models, heuristics and chains, AI analyst guide,
reports); ask for it. In this folder, in order:

1. [SETUP.md](SETUP.md): run it on your machine, environment variables, tests.
2. [ARCHITECTURE.md](ARCHITECTURE.md): how the pieces fit, the result contract, where to extend.
3. [DATA_SOURCES.md](DATA_SOURCES.md): every API and list, its cost and its licence limits.
4. [DEPLOY.md](DEPLOY.md): frontend on Vercel, backend on Railway.
5. [ML_PIPELINE.md](ML_PIPELINE.md): how the training data and models are built, results, retraining.
6. [MODEL_DOSSIERS.md](MODEL_DOSSIERS.md): Random Forest, XGBoost, logistic regression and SMOTE.
7. [STATUS.md](STATUS.md): milestones M1-M16, next steps and known gaps.

Rule-pack authors: copy `backend/rule_packs/_TEMPLATE.yaml` and check it with
`python -m app.redflags validate`. The main [README](../README.md) describes each feature for users.

The project in one paragraph: a browser dashboard for investigating a Bitcoin, Ethereum, Base or
Solana address or transaction, an x402 agent payment, or a website. It fetches live data through
failover-protected public APIs, checks every address against downloaded sanctions, ransomware and
scam lists, grows the money flow as an interactive graph, scores Bitcoin addresses with XGBoost and
Random Forest models explained with SHAP, runs authority red-flag rule packs (FATF, FinCEN, AUSTRAC,
OFAC), lets an AI analyst explain the case from its facts, saves cases to Postgres and exports
evidence. Every finding links to its evidence, source and retrieval time, and every result says what
it does not prove.
