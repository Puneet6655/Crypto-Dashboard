"""Write models/known_labels.parquet: the known illicit/licit Bitcoin addresses used by live scoring.

Run from backend/ (needs data/raw/elliptic_pp/wallets_classes.csv and data/raw/indicators/bitcoin_labels.csv):
    ..\\.venv\\Scripts\\python.exe -m training.known_labels
See app/ml/known_labels.py for the rules.
"""
from app.ml.known_labels import FILE_NAME, build_table
from training.bq_common import BACKEND_DIR


def main() -> None:
    table = build_table(BACKEND_DIR)
    out = BACKEND_DIR / "models" / FILE_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(out, index=False)
    print(f"saved {len(table):,} addresses to {out}")
    print(table.groupby(["source", "cls"]).size().to_string())


if __name__ == "__main__":
    main()
