"""Known-label Bitcoin addresses for the exposure features of live and recent scoring.

illicit = Elliptic++ class 1 + Ransomwhere + OFAC (XBT);  licit = Elliptic++ class 2.
An address on both lists counts as illicit (as in training/exposure.label_table).

Training used only labels known before each time step. Live scoring happens after every label
was published, so here every label counts.

Built once into models/known_labels.parquet (address, cls 1/0, source) by
    ..\\.venv\\Scripts\\python.exe -m training.known_labels
If that file is missing, the raw files are read instead (Elliptic++ CSV and the indicator CSV).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

FILE_NAME = "known_labels.parquet"


@dataclass(frozen=True)
class KnownLabels:
    illicit: frozenset[str]
    licit: frozenset[str]
    source: str  # where the sets were read from, for evidence

    @property
    def counts(self) -> dict[str, int]:
        return {"illicit": len(self.illicit), "licit": len(self.licit)}


def build_table(backend_dir: Path):
    """(address, cls, source) from the raw files. Needs pandas."""
    import pandas as pd

    frames = []
    elliptic = backend_dir / "data" / "raw" / "elliptic_pp" / "wallets_classes.csv"
    if elliptic.exists():
        classes = pd.read_csv(elliptic, dtype={"address": "string", "class": "int8"}).drop_duplicates("address")
        classes = classes[classes["class"].isin([1, 2])]
        frames.append(pd.DataFrame({"address": classes["address"].astype(str),
                                    "cls": (classes["class"] == 1).astype("int8"), "source": "elliptic_pp"}))
    external = backend_dir / "data" / "raw" / "indicators" / "bitcoin_labels.csv"
    if external.exists():
        ext = pd.read_csv(external, dtype=str, usecols=["address", "source"])
        frames.append(pd.DataFrame({"address": ext["address"], "cls": 1, "source": ext["source"]}))
    if not frames:
        return pd.DataFrame({"address": pd.Series(dtype=str), "cls": pd.Series(dtype="int8"),
                             "source": pd.Series(dtype=str)})
    table = pd.concat(frames, ignore_index=True)
    table["cls"] = table["cls"].astype("int8")
    table = table.sort_values(["address", "cls"], ascending=[True, False])
    return table.drop_duplicates("address").reset_index(drop=True)


def load(models_dir: Path) -> KnownLabels:
    import pandas as pd

    path = models_dir / FILE_NAME
    if path.exists():
        table, source = pd.read_parquet(path), str(path.name)
    else:
        table, source = build_table(models_dir.parent), "raw Elliptic++ and indicator files"
    illicit = frozenset(table.loc[table.cls == 1, "address"].astype(str))
    licit = frozenset(table.loc[table.cls == 0, "address"].astype(str))
    return KnownLabels(illicit=illicit, licit=licit, source=source)
