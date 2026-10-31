"""Multi-hop graph features from the saved BigQuery transactions (training/transaction_store.py).

Run from backend/ after transaction_store and build_training_dataset:
    python -m training.hop_features            # all steps 1-101, about 1-2 hours
    python -m training.hop_features --steps 20-21

Graph, per time step: addresses are nodes; two addresses are linked when one paid the other in a
saved transaction (any input address to any output address of the same transaction, never an
address to itself). The saved transactions of a step are the 100 newest transactions of every
training row in its window (4,032 blocks ending at the snapshot), so the graph holds nothing after
the snapshot.

Seeds: addresses whose label was known strictly before the step (training.exposure.label_table):
  illicit = Elliptic++ class 1 from a step before the row's step, plus Ransomwhere addresses first
  seen before the step; licit = Elliptic++ class 2 from an earlier step. OFAC has no dates: never used.
A row's own label never counts: every feature describes the other seeds around it.

Hubs: addresses with more than HUB_COUNTERPARTIES counterparties in the step's graph. A path may
end at a hub but never passes through one (seeds always expand to their direct counterparties).

Features per row, for illicit (and the same with licit_ for licit seeds):
  illicit_hop_dist        hops to the nearest other illicit seed, 1-5; 6 = none within 5 hops
  illicit_hop_dist_2      the same capped for the 1-2 hop set: 1, 2, or 3 = none within 2 hops
  illicit_within_1/_2     distinct illicit seeds within 1 / 2 hops (exact)
  illicit_btc_share_1hop  share of the row's BTC (sent + received, its own 100 transactions) that
                          came from or went to illicit seeds in the same transaction, split by value
  illicit_decayed_2/_5    sum over the row's counterparties of 0.5^hops to the nearest seed through
                          that counterparty (paths up to 2 / 5 hops)
Neighbourhood (graph versions computed for every address from the saved transactions):
  hop2_mean_log_total_txs, hop2_mean_log_btc_received, hop2_mean_log_counterparties: mean of
  log1p(value) over the addresses up to 2 hops away (2-hop walks, not through hubs, excluding the row).
For the alert queue it also keeps, for rows within 2 hops of an illicit seed, the nearest seed and
the transaction hashes on the path.
"""
import argparse
import json
import time

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

from training import exposure
from training.bq_common import BACKEND_DIR, OUT_DIR
from training.build_training_dataset import DATASET, load_elliptic, load_external
from training.time_steps import LAST_STEP
from training.transaction_store import STORE

HUB_COUNTERPARTIES = 1000
MAX_HOPS = 5
NONE = MAX_HOPS + 1
OUT = BACKEND_DIR / "data" / "processed" / "hop_features"
SATS = 1e8

ILLICIT_COLS = ["illicit_hop_dist", "illicit_hop_dist_2", "illicit_within_1", "illicit_within_2",
                "illicit_btc_share_1hop", "illicit_decayed_2", "illicit_decayed_5"]
LICIT_COLS = [c.replace("illicit", "licit") for c in ILLICIT_COLS]
MEAN_COLS = ["hop2_mean_log_total_txs", "hop2_mean_log_btc_received", "hop2_mean_log_counterparties"]
HOP_COLS_5 = ["illicit_hop_dist", "illicit_within_1", "illicit_within_2", "illicit_btc_share_1hop", "illicit_decayed_5",
              "licit_hop_dist", "licit_within_1", "licit_within_2", "licit_btc_share_1hop", "licit_decayed_5"] + MEAN_COLS
HOP_COLS_2 = ["illicit_hop_dist_2", "illicit_within_1", "illicit_within_2", "illicit_btc_share_1hop", "illicit_decayed_2",
              "licit_hop_dist_2", "licit_within_1", "licit_within_2", "licit_btc_share_1hop", "licit_decayed_2"] + MEAN_COLS


def gather(indptr: np.ndarray, nodes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(source node, CSR position) for every neighbour of the given nodes."""
    starts = indptr[nodes]
    lens = indptr[nodes + 1] - starts
    total = int(lens.sum())
    if total == 0:
        return np.empty(0, np.int64), np.empty(0, np.int64)
    src = np.repeat(nodes, lens)
    offsets = np.repeat(starts - np.concatenate([[0], np.cumsum(lens)[:-1]]), lens)
    return src, offsets + np.arange(total)


class StepGraph:
    """Address graph of one step, built from that step's saved transactions."""

    def __init__(self, step: int):
        table = pq.read_table(STORE / f"step={step:03d}" / "part-0.parquet",
                              columns=["tx_hash", "inputs", "outputs", "row_addresses"])
        self.tx_hash = table.column("tx_hash").to_numpy(zero_copy_only=False)
        n_tx = len(self.tx_hash)
        sides = {}
        for side in ("inputs", "outputs"):
            col = table.column(side).combine_chunks()
            parent = pc.list_parent_indices(col).to_numpy()
            flat = pc.list_flatten(col)
            addr = flat.field("address").to_numpy(zero_copy_only=False)
            value = flat.field("value").to_numpy(zero_copy_only=False).astype(np.float64)
            value = np.nan_to_num(value)
            sides[side] = (parent, addr, value)
            total = np.bincount(parent, weights=value, minlength=n_tx)
            setattr(self, f"total_{side}", total)
        known = np.concatenate([sides["inputs"][1], sides["outputs"][1]])
        known = known[pd.notna(known)]
        codes, self.addresses = pd.factorize(known)
        self.index = pd.Index(self.addresses)
        self.n = len(self.addresses)

        # (tx, node, value) per side, summed when an address appears twice on one side.
        legs = {}
        for side, (parent, addr, value) in sides.items():
            ok = pd.notna(addr)
            node = self.index.get_indexer(addr[ok])
            frame = pd.DataFrame({"tx": parent[ok], "node": node, "value": value[ok]})
            legs[side] = frame.groupby(["tx", "node"], as_index=False, sort=True)["value"].sum()
        self.legs_in, self.legs_out = legs["inputs"], legs["outputs"]

        # Every input address linked with every output address of the same transaction.
        ins = self.legs_in[["tx", "node"]].to_numpy()
        outs = self.legs_out[["tx", "node"]].to_numpy()
        out_start = np.searchsorted(outs[:, 0], np.arange(n_tx + 1))
        n_out = np.diff(out_start)
        rep = n_out[ins[:, 0]]
        # Built one undirected pair at a time (low node first) to keep memory down, then mirrored.
        total = int(rep.sum())
        pos = (np.repeat(out_start[ins[:, 0]] - np.concatenate([[0], np.cumsum(rep)[:-1]]), rep)
               + np.arange(total, dtype=np.int64))
        u = np.repeat(ins[:, 1], rep)
        v = outs[pos, 1]
        del pos
        keep = u != v
        key = np.minimum(u, v)[keep] * self.n + np.maximum(u, v)[keep]
        del u, v
        tx = np.repeat(ins[:, 0], rep)[keep]
        del keep
        key, first = np.unique(key, return_index=True)
        tx = tx[first]
        del first
        lo, hi = key // self.n, key % self.n
        del key
        both = np.concatenate([lo * self.n + hi, hi * self.n + lo])
        order = np.argsort(both)  # CSR order: by (src, dst)
        both = both[order]
        self.src = both // self.n
        self.dst = both % self.n
        del both, lo, hi
        self.edge_tx = np.concatenate([tx, tx])[order]
        del order, tx
        self.indptr = np.concatenate([[0], np.cumsum(np.bincount(self.src, minlength=self.n))])
        self.degree = np.diff(self.indptr)
        self.hub = self.degree > HUB_COUNTERPARTIES
        self.pair_count = int(rep.sum())

        # Training rows this transaction was kept for (for the row's own BTC flows).
        rows = table.column("row_addresses").combine_chunks()
        self.kept = pd.DataFrame({"tx": pc.list_parent_indices(rows).to_numpy(),
                                  "node": self.index.get_indexer(pc.list_flatten(rows).to_numpy(zero_copy_only=False))})

        # Graph versions of a few wallet features, for every address.
        tx_count = pd.concat([self.legs_in[["tx", "node"]], self.legs_out[["tx", "node"]]]).drop_duplicates()
        self.node_txs = np.bincount(tx_count["node"], minlength=self.n).astype(np.float64)
        self.node_received = np.bincount(self.legs_out["node"], weights=self.legs_out["value"], minlength=self.n) / SATS

    def nodes_of(self, addresses) -> np.ndarray:
        return self.index.get_indexer(addresses)

    def bfs(self, seed_nodes: np.ndarray):
        """Multi-source BFS: (dist, owner seed, parent node, parent tx) per node; -1 / NONE if not reached."""
        dist = np.full(self.n, NONE, np.int8)
        owner = np.full(self.n, -1, np.int64)
        parent = np.full(self.n, -1, np.int64)
        ptx = np.full(self.n, -1, np.int64)
        is_seed = np.zeros(self.n, bool)
        is_seed[seed_nodes] = True
        dist[seed_nodes] = 0
        owner[seed_nodes] = seed_nodes
        frontier = seed_nodes
        for h in range(1, MAX_HOPS + 1):
            expand = frontier[is_seed[frontier] | ~self.hub[frontier]]
            src, pos = gather(self.indptr, expand)
            nbr = self.dst[pos]
            new = dist[nbr] == NONE
            src, pos, nbr = src[new], pos[new], nbr[new]
            nbr, first = np.unique(nbr, return_index=True)
            dist[nbr] = h
            owner[nbr] = owner[src[first]]
            parent[nbr] = src[first]
            ptx[nbr] = self.edge_tx[pos[first]]
            frontier = nbr
            if len(frontier) == 0:
                break
        return dist, owner, parent, ptx, is_seed

    def seed_features(self, seed_nodes: np.ndarray, rows: np.ndarray, prefix: str, want_paths: bool = False) -> dict:
        dist, owner, parent, ptx, is_seed = self.bfs(seed_nodes)
        can_pass = is_seed | ~self.hub
        n_rows = len(rows)

        # Hop distance and decayed sum, through each counterparty, never counting the row itself.
        src, pos = gather(self.indptr, rows)
        row_of = np.repeat(np.arange(n_rows), self.degree[rows])
        nbr = self.dst[pos]
        d_nbr = dist[nbr].astype(np.int64)
        via = (d_nbr < NONE) & (owner[nbr] != rows[row_of]) & ((d_nbr == 0) | can_pass[nbr])
        hops = np.where(via, d_nbr + 1, NONE + 1)
        best = np.full(n_rows, NONE + 1, np.int64)
        np.minimum.at(best, row_of, hops)
        hop_dist = np.minimum(best, NONE)
        dec2 = np.bincount(row_of[hops <= 2], weights=0.5 ** hops[hops <= 2], minlength=n_rows)
        dec5 = np.bincount(row_of[hops <= MAX_HOPS], weights=0.5 ** hops[hops <= MAX_HOPS], minlength=n_rows)

        # Distinct seeds within 1 and 2 hops (exact), enumerated from the rows in batches.
        row_mask = np.zeros(self.n, bool)
        row_mask[rows] = True
        within1_arr = np.bincount(row_of[is_seed[nbr]], minlength=n_rows).astype(np.float64)
        e_src, e_pos = gather(self.indptr, seed_nodes)  # seed -> neighbour m, as a CSR of seeds per m
        m = self.dst[e_pos]
        order = np.argsort(m, kind="stable")
        seed_by_m = e_src[order]
        seed_ptr = np.concatenate([[0], np.cumsum(np.bincount(m, minlength=self.n))])
        seed_cnt = np.diff(seed_ptr)
        via_m = can_pass[nbr]  # paths row - m - seed: m must not be a hub
        per_row = np.bincount(row_of, weights=np.where(via_m, seed_cnt[nbr], 0), minlength=n_rows)
        within2_arr = np.zeros(n_rows)
        budget, start = 40_000_000, 0
        cum = np.cumsum(per_row)
        row_start = np.concatenate([[0], np.cumsum(self.degree[rows])])
        while start < n_rows:
            base = cum[start - 1] if start else 0.0
            end = max(start + 1, int(np.searchsorted(cum, base + budget, side="right")))
            end = min(end, n_rows)
            sl = slice(row_start[start], row_start[end])
            b_row, b_nbr = row_of[sl], nbr[sl]
            sel = via_m[sl]
            mids, mrow = b_nbr[sel], b_row[sel]
            s_src, s_pos = gather(seed_ptr, mids)
            s = seed_by_m[s_pos]
            r_idx = np.repeat(mrow, seed_cnt[mids])
            direct = is_seed[b_nbr]
            r_all = np.concatenate([r_idx, b_row[direct]])
            s_all = np.concatenate([s, b_nbr[direct]])
            ok = s_all != rows[r_all]
            key = np.unique(r_all[ok].astype(np.int64) * self.n + s_all[ok])
            within2_arr += np.bincount((key // self.n).astype(np.int64), minlength=n_rows)
            start = end
        within1 = pd.Series(within1_arr, index=rows)
        within2 = pd.Series(within2_arr, index=rows)

        # BTC share with seeds in the row's own transactions.
        seed_in = np.bincount(self.legs_in.tx, weights=self.legs_in.value * is_seed[self.legs_in.node], minlength=len(self.tx_hash))
        seed_out = np.bincount(self.legs_out.tx, weights=self.legs_out.value * is_seed[self.legs_out.node], minlength=len(self.tx_hash))
        kept = self.kept[row_mask[self.kept.node]]
        k = kept.merge(self.legs_in.rename(columns={"value": "v_in"}), on=["tx", "node"], how="left")
        k = k.merge(self.legs_out.rename(columns={"value": "v_out"}), on=["tx", "node"], how="left").fillna(0.0)
        tx, node = k.tx.to_numpy(), k.node.to_numpy()
        self_seed = is_seed[node]
        s_in = seed_in[tx] - np.where(self_seed, k.v_in, 0.0)
        s_out = seed_out[tx] - np.where(self_seed, k.v_out, 0.0)
        tot_in = np.where(self.total_inputs[tx] > 0, self.total_inputs[tx], 1.0)
        tot_out = np.where(self.total_outputs[tx] > 0, self.total_outputs[tx], 1.0)
        exposed = k.v_out * np.clip(s_in / tot_in, 0, 1) + k.v_in * np.clip(s_out / tot_out, 0, 1)
        flows = pd.DataFrame({"node": node, "exposed": exposed, "flow": k.v_in + k.v_out}).groupby("node").sum()
        share = (flows.exposed / flows.flow.where(flows.flow > 0)).fillna(0.0)

        out = {
            f"{prefix}_hop_dist": hop_dist,
            f"{prefix}_hop_dist_2": np.where(hop_dist <= 2, hop_dist, 3),
            f"{prefix}_within_1": within1.to_numpy(),
            f"{prefix}_within_2": within2.to_numpy(),
            f"{prefix}_btc_share_1hop": share.reindex(rows).fillna(0).to_numpy(),
            f"{prefix}_decayed_2": dec2,
            f"{prefix}_decayed_5": dec5,
        }
        if want_paths:
            out.update(self.paths(rows, row_of, nbr, pos, hops, hop_dist, owner, parent, ptx))
        return out

    def paths(self, rows, row_of, nbr, pos, hops, hop_dist, owner, parent, ptx) -> dict:
        """Nearest seed and the transaction hashes on the path, for rows within 2 hops."""
        n_rows = len(rows)
        first_nbr = np.full(n_rows, -1, np.int64)
        first_tx = np.full(n_rows, -1, np.int64)
        order = np.lexsort((hops, row_of))  # per row, smallest hop first
        r_sorted, h_sorted = row_of[order], hops[order]
        lead = np.r_[True, r_sorted[1:] != r_sorted[:-1]] & (h_sorted <= 2)
        first_nbr[r_sorted[lead]] = nbr[order][lead]
        first_tx[r_sorted[lead]] = self.edge_tx[pos[order][lead]]
        seed = np.full(n_rows, None, object)
        txs = np.full(n_rows, None, object)
        for i in np.flatnonzero(hop_dist <= 2):
            n = first_nbr[i]
            chain = [self.tx_hash[first_tx[i]]]
            if hop_dist[i] == 2:  # row - n - seed
                chain.append(self.tx_hash[ptx[n]])
            seed[i] = self.addresses[owner[n]]
            txs[i] = chain
        return {"illicit_nearest_seed": seed, "illicit_path_txs": txs}

    def neighbour_means(self, rows: np.ndarray) -> dict:
        """Mean of log1p(feature) over 2-hop walks from each row (not through hubs, not back to the row)."""
        feats = {"hop2_mean_log_total_txs": np.log1p(self.node_txs),
                 "hop2_mean_log_btc_received": np.log1p(self.node_received),
                 "hop2_mean_log_counterparties": np.log1p(self.degree.astype(np.float64))}
        passable = ~self.hub
        out = {}
        for name, f in feats.items():
            s1 = np.bincount(self.src, weights=f[self.dst], minlength=self.n)  # sum over neighbours
            # 2-hop walks r - n - u through a non-hub n, u != r: sum of f(u) and their count.
            through = np.where(passable, s1, 0.0)
            through_cnt = np.where(passable, self.degree - 1, 0).astype(np.float64)
            s2 = np.bincount(self.src, weights=through[self.dst], minlength=self.n)
            c2 = np.bincount(self.src, weights=through_cnt[self.dst], minlength=self.n)
            back = np.bincount(self.src, weights=passable[self.dst].astype(np.float64), minlength=self.n)
            total = s1 + s2 - back * f  # remove walks that return to the row
            count = self.degree + c2
            out[name] = np.where(count > 0, total / np.maximum(count, 1), 0.0)[rows]
        return out


def labels() -> pd.DataFrame:
    rows, classes = load_elliptic()
    steps = pd.read_csv(OUT_DIR / "time_steps.csv", parse_dates=["snap_start_time", "snap_end_time"])
    return exposure.label_table(rows, classes, load_external(), steps)


def step_features(step: int, row_addresses: pd.Series, label_table: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    t0 = time.time()
    g = StepGraph(step)
    built = time.time() - t0
    rows = g.nodes_of(row_addresses)
    if (rows < 0).any():
        raise RuntimeError(f"step {step}: {(rows < 0).sum()} training rows have no saved transactions")
    known = label_table[label_table.known_after_step < step]
    illicit = g.nodes_of(known.loc[known.cls == 1, "address"])
    licit = g.nodes_of(known.loc[known.cls == 0, "address"])
    illicit, licit = np.unique(illicit[illicit >= 0]), np.unique(licit[licit >= 0])
    data = {"address": row_addresses.to_numpy(), "step": step}
    data.update(g.seed_features(illicit, rows, "illicit", want_paths=True))
    data.update(g.seed_features(licit, rows, "licit"))
    data.update(g.neighbour_means(rows))
    stats = {"step": step, "transactions": len(g.tx_hash), "addresses": g.n, "links": len(g.src) // 2,
             "pairs_before_dedupe": g.pair_count, "hubs": int(g.hub.sum()), "rows": len(rows),
             "rows_that_are_hubs": int(g.hub[rows].sum()), "illicit_seeds": len(illicit),
             "illicit_seed_hubs": int(g.hub[illicit].sum()), "licit_seeds": len(licit),
             "seconds_build": round(built, 1), "seconds_total": round(time.time() - t0, 1)}
    return pd.DataFrame(data), stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--steps", default=f"1-{LAST_STEP}")
    args = parser.parse_args()
    lo, hi = (int(x) for x in args.steps.split("-"))
    table = labels()
    all_rows = pd.read_parquet(DATASET, columns=["address", "step"])
    OUT.mkdir(parents=True, exist_ok=True)
    for step in range(lo, hi + 1):
        target = OUT / f"step={step:03d}.parquet"
        if target.exists() or not (STORE / f"step={step:03d}" / "part-0.parquet").exists():
            continue  # done, or its transactions are not downloaded yet
        rows = all_rows.loc[all_rows.step == step, "address"].astype(str).reset_index(drop=True)
        frame, stats = step_features(step, rows, table)
        frame.to_parquet(target, index=False)
        (OUT / f"step={step:03d}.json").write_text(json.dumps(stats))
        print(json.dumps(stats), flush=True)


if __name__ == "__main__":
    main()
