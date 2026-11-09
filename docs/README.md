# Handover

Start here if you are new to the project. In order:

1. [SETUP.md](SETUP.md): run it on your machine, environment variables, tests.
2. [ARCHITECTURE.md](ARCHITECTURE.md): how the pieces fit, the result contract, where to extend.
3. [DATA_SOURCES.md](DATA_SOURCES.md): every API and list, its cost and its licence limits.
4. [ML_PIPELINE.md](ML_PIPELINE.md): how the training data and models are built, results, retraining.
5. [MODEL_DOSSIERS.md](MODEL_DOSSIERS.md): Random Forest, XGBoost, logistic regression and SMOTE.
6. [STATUS.md](STATUS.md): milestones M1-M16, next steps and known gaps.

Detailed experiment reports (numbers, methods, costs) are in `backend/reports/`. The main
[README](../README.md) has the user-facing description of each feature.

The project in one paragraph: a browser dashboard for investigating a Bitcoin (and Ethereum, Base or
Solana) address, transaction or website. It fetches live data through failover-protected public
APIs, checks every address against downloaded sanctions, ransomware and scam lists, draws the money
flow as a graph with NetworkX measures, saves cases to Postgres, and compares Random Forest, XGBoost
and a logistic-regression baseline on an offline Elliptic++ benchmark with SHAP explanations. Every
finding links to its evidence, source and retrieval time, and every result says what it does not
prove.
