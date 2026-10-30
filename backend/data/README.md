# Data folder

Everything under `raw/` is ignored by git. Never commit dataset files.

## Elliptic++ (Milestone 4)

Download from the Google Drive folder linked at https://github.com/git-disl/EllipticPlusPlus
and put the files here, flat (no sub-folders):

```
backend/data/raw/elliptic_pp/
    txs_features.csv
    txs_classes.csv
    txs_edgelist.csv
    wallets_features.csv
    wallets_classes.csv
    AddrAddr_edgelist.csv
    AddrTx_edgelist.csv
    TxAddr_edgelist.csv
```

The inspection script needs only the four `*_features.csv` and `*_classes.csv` files.
The edgelists are for the graph milestones later.

Then, from `backend/` with the virtual environment active:

```
pip install -r requirements-ml.txt
python -m training.inspect_elliptic --show-columns
python -m training.inspect_elliptic
```

The first command prints the real column names. The script guesses names such as
`address`, `txId`, `Time step` and `class`; if a guess is wrong it stops and lists the
real columns. The second command writes `backend/reports/elliptic_pp_data_card.md`.

## BigQuery public Bitcoin data (training dataset)

Recomputes wallet features from all on-chain transactions for the Elliptic++ period
(Dec 2015 to Sep 2017) and the two years after it, using the same rules as the live
mempool.space lookup (`app/features/wallet_window.py`). Details, dates and costs:
`reports/bigquery_training_dataset.md`.

1. Put the Google Cloud service-account key in this folder (any `*.json` here is gitignored),
   or set `BQ_KEY_PATH` to its path. Never commit it.
2. From `backend/`:

```
..\.venv\Scripts\python.exe -m pip install -r requirements-ml.txt
..\.venv\Scripts\python.exe -m training.pull_bigquery plan     # prints the GB each query would scan
..\.venv\Scripts\python.exe -m training.pull_bigquery run      # about 175 GB of the 1 TB monthly free tier
..\.venv\Scripts\python.exe -m training.build_training_dataset
..\.venv\Scripts\python.exe -m training.check_live_parity      # optional: compare rows with live data
```

Outputs go to `processed/` (gitignored): `processed/training_dataset.parquet`, plus
`processed/bigquery/` with per-block and daily network stats, the time-step dates and a cost log.
A rerun within 24 hours reuses BigQuery's stored results instead of paying again.
