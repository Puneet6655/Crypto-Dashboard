"""Check that the BigQuery SQL and app/features/wallet_window.py compute the same features.

Runs the real SQL on a small slice (one time step, a short window), downloads the raw
transactions of a sample of those addresses, recomputes their features in Python and
compares every column. Uses about 1 GB of the BigQuery free tier.

    python -m training.check_sql_parity --step 25 --window 300 --max-txs 20
"""
import argparse
import math

import pandas as pd
from google.cloud import bigquery

from app.features.wallet_window import FEATURE_NAMES, TxRecord, compute_wallet_features, window_counterparties
from training import bq_common
from training.pull_bigquery import PLAN_STANDIN, STD_TYPES, build_queries, month_bounds
from training.time_steps import steps_frame

RAW_TX_SQL = """
SELECT
  `hash` AS txid, block_number, block_timestamp,
  IF(is_coinbase, 0, CAST(fee AS INT64)) AS fee,
  IF(is_coinbase, 0, CAST(input_value AS INT64)) AS input_value,
  virtual_size AS vsize, input_count, output_count,
  ARRAY(SELECT AS STRUCT i.addresses[OFFSET(0)] AS address, CAST(i.value AS INT64) AS value,
                         FARM_FINGERPRINT(i.addresses[OFFSET(0)]) AS fp
        FROM UNNEST(inputs) AS i WHERE i.type IN UNNEST(@std_types) AND ARRAY_LENGTH(i.addresses) = 1) AS ins,
  ARRAY(SELECT AS STRUCT o.addresses[OFFSET(0)] AS address, CAST(o.value AS INT64) AS value,
                         FARM_FINGERPRINT(o.addresses[OFFSET(0)]) AS fp
        FROM UNNEST(outputs) AS o WHERE o.type IN UNNEST(@std_types) AND ARRAY_LENGTH(o.addresses) = 1) AS outs
FROM `bigquery-public-data.crypto_bitcoin.transactions`
WHERE block_timestamp_month BETWEEN @month_lo AND @month_hi
  AND block_number BETWEEN @block_lo AND @block_hi
  AND (EXISTS(SELECT 1 FROM UNNEST(inputs) AS i, UNNEST(i.addresses) AS a WHERE a IN UNNEST(@addresses))
       OR EXISTS(SELECT 1 FROM UNNEST(outputs) AS o, UNNEST(o.addresses) AS a WHERE a IN UNNEST(@addresses)))
"""


def close(a: float, b: float) -> bool:
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--step", type=int, default=25)
    parser.add_argument("--window", type=int, default=300)
    parser.add_argument("--max-txs", type=int, default=20)
    parser.add_argument("--week", type=int, default=100, help="short-window length for the test")
    parser.add_argument("--sample", type=int, default=300)
    args = parser.parse_args()

    client = bq_common.get_client()
    steps = steps_frame()
    steps = steps[steps.step == args.step]
    snap_end = int(steps.snap_end.iloc[0])
    block_lo = snap_end - args.window + 1
    months = month_bounds(client, block_lo, snap_end)

    plan = build_queries(steps, months, 0, PLAN_STANDIN, args.window, args.max_txs, week_blocks=args.week)
    active = bq_common.run_query(client, *plan["active_addresses"], label="parity_active", max_gb=2)
    real = build_queries(steps, months, 0, f"`{bq_common.result_table_id(active)}`", args.window, args.max_txs,
                         week_blocks=args.week)
    job = bq_common.run_query(client, *real["wallet_features"], label="parity_features", max_gb=3)
    sql_rows = job.to_dataframe(create_bqstorage_client=False)
    print(f"SQL rows for step {args.step}: {len(sql_rows):,}")

    # A mix: random rows, the busiest wallets (cap and truncation), and senders (counterparties).
    picks = pd.concat([
        sql_rows.sample(min(args.sample, len(sql_rows)), random_state=1),
        sql_rows.nlargest(30, "n_in_window"),
        sql_rows[sql_rows.num_txs_as_sender > 0].nlargest(30, "num_counterparties"),
    ]).drop_duplicates("address")
    params = [
        bigquery.ScalarQueryParameter("month_lo", "DATE", months[0]),
        bigquery.ScalarQueryParameter("month_hi", "DATE", months[1]),
        bigquery.ScalarQueryParameter("block_lo", "INT64", block_lo),
        bigquery.ScalarQueryParameter("block_hi", "INT64", snap_end),
        bigquery.ArrayQueryParameter("std_types", "STRING", STD_TYPES),
        bigquery.ArrayQueryParameter("addresses", "STRING", picks.address.tolist()),
    ]
    raw = bq_common.run_query(client, RAW_TX_SQL, params, label="parity_raw_txs", max_gb=3).to_dataframe(
        create_bqstorage_client=False)
    records = [
        TxRecord(
            txid=r.txid, block_height=int(r.block_number), block_time=r.block_timestamp.to_pydatetime(),
            fee_sats=int(r.fee), input_value_sats=int(r.input_value),
            inputs=tuple((x["address"], int(x["value"])) for x in r.ins),
            outputs=tuple((x["address"], int(x["value"])) for x in r.outs),
            vsize=int(r.vsize), input_count=int(r.input_count), output_count=int(r.output_count),
        )
        for r in raw.itertuples()
    ]
    fingerprint = {x["address"]: int(x["fp"]) for r in raw.itertuples() for x in list(r.ins) + list(r.outs)}
    print(f"raw transactions downloaded: {len(records):,} for {len(picks)} addresses")

    by_address: dict[str, list[TxRecord]] = {}
    for rec in records:
        for address in {a for a, _ in rec.inputs} | {a for a, _ in rec.outputs}:
            by_address.setdefault(address, []).append(rec)

    mismatches: dict[str, int] = {}
    examples = []
    for row in picks.itertuples():
        python = compute_wallet_features(row.address, by_address.get(row.address, []), snap_end,
                                         args.window, args.max_txs, args.week)
        for name in FEATURE_NAMES + ("anchor_block",):
            if not close(python[name] or 0, getattr(row, name)):
                mismatches[name] = mismatches.get(name, 0) + 1
                if len(examples) < 10:
                    examples.append((row.address, name, getattr(row, name), python[name]))
        payers, payees = window_counterparties(row.address, by_address.get(row.address, []), snap_end,
                                               args.window, args.max_txs)
        for name, mine, theirs in (("payer_fps", payers, row.payer_fps), ("payee_fps", payees, row.payee_fps)):
            if {fingerprint[a] for a in mine} != {int(f) for f in theirs}:
                mismatches[name] = mismatches.get(name, 0) + 1
                if len(examples) < 10:
                    examples.append((row.address, name, len(theirs), len(mine)))
    print(f"compared {len(picks)} wallets x {len(FEATURE_NAMES) + 1} values + payer/payee lists")
    if mismatches:
        print("MISMATCHES per feature:", mismatches)
        for example in examples:
            print("  ", example)
        raise SystemExit(1)
    print("All values match.")


if __name__ == "__main__":
    main()
