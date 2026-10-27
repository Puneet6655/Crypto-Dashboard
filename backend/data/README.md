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
