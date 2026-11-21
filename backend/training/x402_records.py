"""Pull a set of real x402 payments on Base for testing and tuning the x402 rules.

Source: Coinbase CDP SQL API (app/x402/cdp.py), with the CDP key in backend/.env. An x402 payment here is a USDC
Transfer on Base whose transaction was sent by a known facilitator (the x402scan / x402analysis approach).

Two parts:
  * sample: `--slices` evenly spaced starting points over the last `--days` days; from each, the next `--per-slice`
    payments in time order. A spread of ordinary traffic.
  * targeted: recent USDC transfers of the addresses the snapshot flags (phishing, phishing-funded, tainted or
    risky servers), kept only when a known facilitator sent them. These make the rules fire.

Every payment is run through the dashboard's own rules (app/x402/rules.py: check_payment) and written with its hits.

Usage (from backend/):
    python -m training.x402_records --days 30 --slices 30 --per-slice 1000 --targets 300

Output goes to data/raw/x402_records/ (gitignored): payments.jsonl, payments.csv and summary.json.
Keep it local test data: do not commit it and do not train a model on it.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND / ".env")

from app.x402.cdp import CdpError, CdpSqlClient  # noqa: E402
from app.x402.rules import Payment, alert_worthy, check_payment, worst  # noqa: E402
from app.x402.snapshot import get_snapshot, norm  # noqa: E402

OUT = BACKEND / "data" / "raw" / "x402_records"
PAUSE = 0.6  # seconds between queries: the free tier allows about 2 a second


def _index():
    try:
        from app.intelligence.index import get_indicator_index
        return get_indicator_index()
    except Exception as exc:  # sanctions lists are optional here
        print(f"  (sanctions index not loaded: {exc}; the sanctions rule is skipped)")
        return None


async def sample(cdp: CdpSqlClient, facilitators: list[str], days: int, slices: int, per_slice: int) -> list[Payment]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    step = (now - start) / slices
    out: list[Payment] = []
    for i in range(slices):
        since = start + step * i
        try:
            got = await cdp.x402_payments(facilitators, since, per_slice)
        except CdpError as exc:
            print(f"  slice {i + 1}/{slices} failed: {exc}")
            got = []
        out.extend(got)
        print(f"  slice {i + 1}/{slices} from {since:%Y-%m-%d %H:%M}: {len(got)} payments")
        await asyncio.sleep(PAUSE)
    return out


def flagged_addresses(snap, limit: int) -> list[tuple[str, str]]:
    seen, out = set(), []

    def add(addr: str, why: str):
        a = norm(addr)
        if a.startswith("0x") and len(a) == 42 and a not in seen:
            seen.add(a)
            out.append((a, why))

    for a in snap.phishing:
        add(a, "phishing")
    for a in snap.phishing_funded:
        add(a, "phishing_funded")
    for a, t in sorted(snap.taint.items(), key=lambda kv: -(kv[1].get("from_phishing_percent") or 0)):
        if (t.get("from_phishing") or 0) > 0 or t.get("phishing_funded"):
            add(a, "tainted_server")
    for s in snap.risky_servers(limit=500):
        add(s.get("address") or s.get("pay_to") or "", "risky_server")
    return out[:limit]


async def targeted(cdp: CdpSqlClient, snap, facilitators: set[str], days: int, limit: int, per_address: int):
    out: list[tuple[Payment, str]] = []
    since = datetime.now(timezone.utc) - timedelta(days=days)
    targets = flagged_addresses(snap, limit)
    for i, (addr, why) in enumerate(targets, 1):
        got = []
        for direction in ("to", "from"):  # one OR query over both sides scans too much for CDP's per-query limit
            try:
                got += await cdp.address_transfers(addr, direction, since, per_address)
            except CdpError as exc:
                print(f"  {i}/{len(targets)} {addr} ({direction}) failed: {str(exc).splitlines()[0][:160]}")
            await asyncio.sleep(PAUSE)
        kept = [p for p in got if p.facilitator in facilitators]
        for p in kept:
            p.method, p.gasless = "transferWithAuthorization", True  # facilitator-sent USDC transfer = x402
            out.append((p, why))
        if i % 25 == 0 or kept:
            print(f"  {i}/{len(targets)} {why} {addr[:10]}: {len(got)} transfers, {len(kept)} via a facilitator")
        await asyncio.sleep(PAUSE)
    return out, len(targets)


def record(snap, p: Payment, origin: str, index) -> dict:
    hits = check_payment(snap, p, index)
    server = snap.server(p.pay_to) or {}
    fac = snap.facilitator(p.facilitator or "") or {}
    return {**p.as_dict(), "origin": origin,
            "server_names": [x.get("name") for x in (server.get("servers") or [])][:5],
            "server_risk": server.get("risk"),
            "facilitator_names": fac.get("names") or [],
            "worst": worst(hits), "alert": alert_worthy(hits),
            "hits": [{"rule": h.rule, "severity": h.severity, "title": h.title, "address": h.address} for h in hits]}


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--slices", type=int, default=30)
    ap.add_argument("--per-slice", type=int, default=1000)
    ap.add_argument("--targets", type=int, default=300, help="flagged addresses to query (0 to skip)")
    ap.add_argument("--target-days", type=int, default=90)
    ap.add_argument("--per-address", type=int, default=50)
    args = ap.parse_args()

    cdp = CdpSqlClient.from_env()
    if cdp is None:
        raise SystemExit("CDP_API_KEY_ID / CDP_API_KEY_SECRET missing in backend/.env (or PyJWT/cryptography).")
    snap = get_snapshot()
    facilitators = snap.facilitator_addresses("base")
    index = _index()
    t0 = time.time()

    print(f"Sample: {args.slices} slices x {args.per_slice} over {args.days} days, {len(facilitators)} facilitators")
    sampled = await sample(cdp, facilitators, args.days, args.slices, args.per_slice)
    tgt, n_targets = ([], 0)
    if args.targets:
        print(f"Targeted: up to {args.targets} flagged addresses, last {args.target_days} days")
        tgt, n_targets = await targeted(cdp, snap, set(facilitators), args.target_days, args.targets,
                                        args.per_address)

    rows, seen = [], set()
    for p, origin in [(p, "sample") for p in sampled] + [(p, f"targeted:{why}") for p, why in tgt]:
        key = (p.tx, p.payer, p.pay_to, p.amount)
        if key in seen:
            continue
        seen.add(key)
        rows.append(record(snap, p, origin, index))

    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "payments.jsonl", "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    cols = ["chain", "tx", "at", "block", "payer", "pay_to", "amount", "facilitator", "origin", "worst", "alert",
            "rules", "server_names", "facilitator_names"]
    with open(OUT / "payments.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({**{c: r.get(c) for c in cols}, "rules": ";".join(h["rule"] for h in r["hits"]),
                        "server_names": "; ".join(r["server_names"]), "facilitator_names": "; ".join(r["facilitator_names"])})

    rule_counts = Counter(h["rule"] for r in rows for h in r["hits"])
    amounts = sorted(r["amount"] for r in rows if r["amount"] is not None)
    summary = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Coinbase CDP SQL API (base.events), USDC Transfer events sent by known x402 facilitators",
        "source_url": "https://docs.cdp.coinbase.com/data/sql-api/welcome",
        "snapshot_built_at": snap.meta.get("built_at") if hasattr(snap, "meta") and isinstance(snap.meta, dict) else None,
        "params": vars(args), "queries": cdp.queries, "rows_read": cdp.rows, "seconds": round(time.time() - t0),
        "payments": len(rows),
        "by_origin": Counter(r["origin"] for r in rows),
        "time_range": [min((r["at"] for r in rows if r["at"]), default=None), max((r["at"] for r in rows if r["at"]), default=None)],
        "distinct": {"payers": len({r["payer"] for r in rows}), "pay_to": len({r["pay_to"] for r in rows}),
                     "facilitators": len({r["facilitator"] for r in rows})},
        "known_server_pay_to": sum(1 for r in rows if r["server_names"]),
        "amount_usdc": {"total": round(sum(amounts), 2), "median": amounts[len(amounts) // 2] if amounts else None,
                        "max": amounts[-1] if amounts else None},
        "with_hits": sum(1 for r in rows if r["hits"]), "alerts": sum(1 for r in rows if r["alert"]),
        "worst": Counter(r["worst"] or "none" for r in rows), "rules": dict(rule_counts.most_common()),
        "flagged_addresses_queried": n_targets, "sanctions_index": index is not None,
        "limitations": [
            "A facilitator-sent USDC transfer is taken as an x402 payment; the method (transferWithAuthorization) is "
            "assumed, not read from each transaction.",
            "Only facilitators in the snapshot (x402analysis + x402scan lists, Oct 2026) are covered; new ones are missed.",
            "Base only. Solana needs a Helius key or another Solana RPC.",
            "Request, verification and delivery happen off-chain; only settlement is visible here.",
        ],
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("payments", "by_origin", "with_hits", "alerts", "worst", "rules",
                                              "queries", "seconds")}, indent=2))
    print(f"Written to {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
