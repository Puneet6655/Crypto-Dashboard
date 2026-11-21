"""Pull real x402 payments on Solana for testing and tuning the x402 rules (the Solana half of x402_records.py).

Source: Helius getTransactionsForAddress (https://www.helius.dev/docs/rpc/gettransactionsforaddress), with
HELIUS_API_KEY in backend/.env. Free plan: 1M credits a month; this call costs 10 credits per 100 transactions.

For every Solana payment address in the x402 snapshot, it reads that address's recent transactions (token accounts
included) and keeps the ones where the address's USDC balance went up. An x402 payment on Solana is such a transfer
whose fee payer (the first signer) is a known facilitator: the facilitator pays the fee, the client signs the transfer.
Transfers with any other fee payer are kept too, marked `x402: false`, as a control set.

Usage (from backend/):
    python -m training.x402_records_solana --days 30 --per-address 300

Output: data/raw/x402_records/solana_payments.jsonl and solana_summary.json (gitignored). Local test data only.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND / ".env")

from app.x402.rules import Payment, alert_worthy, check_payment, worst  # noqa: E402
from app.x402.snapshot import USDC_SOLANA, get_snapshot  # noqa: E402

OUT = BACKEND / "data" / "raw" / "x402_records"
URL = "https://mainnet.helius-rpc.com/"
PAUSE = 0.15  # free plan: 10 RPC requests a second


def _key(k: dict | str) -> str:
    return k["pubkey"] if isinstance(k, dict) else k


def usdc_changes(tx: dict) -> dict[str, float]:
    """Owner -> change in USDC balance in this transaction."""
    meta = tx.get("meta") or {}
    bal: dict[str, float] = {}
    for sign, rows in ((-1, meta.get("preTokenBalances") or []), (1, meta.get("postTokenBalances") or [])):
        for b in rows:
            if b.get("mint") != USDC_SOLANA or not b.get("owner"):
                continue
            amt = int((b.get("uiTokenAmount") or {}).get("amount") or 0) / 1e6
            bal[b["owner"]] = bal.get(b["owner"], 0.0) + sign * amt
    return {o: round(v, 6) for o, v in bal.items() if abs(v) > 0}


def fetch(client: httpx.Client, key: str, address: str, since: int, want: int, stats: Counter) -> list[dict]:
    out, token = [], None
    while len(out) < want:
        cfg = {"transactionDetails": "full", "encoding": "jsonParsed", "maxSupportedTransactionVersion": 1,
               "limit": min(100, want - len(out)), "sortOrder": "desc",
               "filters": {"tokenAccounts": "balanceChanged", "status": "succeeded", "blockTime": {"gte": since}}}
        if token:
            cfg["paginationToken"] = token
        body = {"jsonrpc": "2.0", "id": 1, "method": "getTransactionsForAddress", "params": [address, cfg]}
        for attempt in range(4):
            r = client.post(URL, params={"api-key": key}, json=body)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            break
        stats["requests"] += 1
        j = r.json()
        if "error" in j:
            raise RuntimeError(j["error"].get("message", str(j["error"]))[:200])
        res = j.get("result") or {}
        data = res.get("data") or []
        stats["transactions"] += len(data)
        out.extend(data)
        token = res.get("paginationToken")
        time.sleep(PAUSE)
        if not token or not data:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--per-address", type=int, default=300, help="most transactions read per payment address")
    ap.add_argument("--addresses", type=int, default=0, help="limit the number of payment addresses (0 = all)")
    args = ap.parse_args()

    key = (os.getenv("HELIUS_API_KEY") or "").strip()
    if not key:
        raise SystemExit("HELIUS_API_KEY missing in backend/.env")
    snap = get_snapshot()
    facilitators = set(snap.facilitator_addresses("solana", include_deprecated=True))
    servers = [a for a, s in snap.servers.items() if s["chain"] == "solana" and 32 <= len(a) <= 44 and not a.startswith("r")]
    servers.sort(key=lambda a: -((snap.taint.get(a) or {}).get("transfers") or 0))
    if args.addresses:
        servers = servers[:args.addresses]
    since = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp())
    try:
        from app.intelligence.index import get_indicator_index
        index = get_indicator_index()
    except Exception:
        index = None

    stats: Counter = Counter()
    rows, seen, t0 = [], set(), time.time()
    with httpx.Client(timeout=60) as client:
        for i, addr in enumerate(servers, 1):
            try:
                txs = fetch(client, key, addr, since, args.per_address, stats)
            except Exception as exc:
                stats["failed_addresses"] += 1
                print(f"  {i}/{len(servers)} {addr[:10]} failed: {exc}")
                continue
            kept = 0
            for tx in txs:
                ch = usdc_changes(tx)
                got = ch.get(addr, 0)
                if got <= 0:
                    continue
                msg = (tx.get("transaction") or {}).get("message") or {}
                keys = [_key(k) for k in msg.get("accountKeys") or []]
                sig = ((tx.get("transaction") or {}).get("signatures") or [""])[0]
                if sig in seen:
                    continue
                seen.add(sig)
                fee_payer = keys[0] if keys else None
                senders = sorted((o for o, v in ch.items() if v < 0), key=lambda o: ch[o])
                payer = senders[0] if senders else None
                at = datetime.fromtimestamp(tx["blockTime"], timezone.utc).isoformat() if tx.get("blockTime") else None
                is_x402 = fee_payer in facilitators and fee_payer != payer
                p = Payment(chain="solana", tx=sig, payer=payer or "", pay_to=addr, amount=round(got, 6),
                            facilitator=fee_payer, at=at, block=tx.get("slot"),
                            method="spl-token transfer" if is_x402 else None, gasless=is_x402)
                hits = check_payment(snap, p, index)
                server = snap.server(addr) or {}
                fac = snap.facilitator(fee_payer or "") or {}
                rows.append({**p.as_dict(), "x402": is_x402, "origin": "solana_server_scan",
                             "server_names": [x.get("name") for x in server.get("servers") or []][:5],
                             "server_risk": server.get("risk"), "facilitator_names": fac.get("names") or [],
                             "worst": worst(hits), "alert": alert_worthy(hits),
                             "hits": [{"rule": h.rule, "severity": h.severity, "title": h.title, "address": h.address}
                                      for h in hits]})
                kept += 1
            if i % 20 == 0 or kept:
                print(f"  {i}/{len(servers)} {addr[:10]}: {len(txs)} txs, {kept} USDC inflows")

    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "solana_payments.jsonl", "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    x = [r for r in rows if r["x402"]]
    amounts = sorted(r["amount"] for r in x)
    summary = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Helius getTransactionsForAddress, USDC balance increases of x402 server payment addresses",
        "source_url": "https://www.helius.dev/docs/rpc/gettransactionsforaddress",
        "params": vars(args), "payment_addresses": len(servers), "seconds": round(time.time() - t0),
        "requests": stats["requests"], "transactions_read": stats["transactions"],
        "credits_estimate": stats["requests"] * 10 + stats["transactions"] // 10,
        "failed_addresses": stats["failed_addresses"],
        "usdc_inflows": len(rows), "x402_payments": len(x),
        "distinct": {"payers": len({r["payer"] for r in x}), "pay_to": len({r["pay_to"] for r in x}),
                     "facilitators": len({r["facilitator"] for r in x})},
        "by_facilitator": Counter(", ".join(r["facilitator_names"]) or r["facilitator"] for r in x).most_common(20),
        "time_range": [min((r["at"] for r in rows if r["at"]), default=None), max((r["at"] for r in rows if r["at"]), default=None)],
        "amount_usdc": {"total": round(sum(amounts), 2), "median": amounts[len(amounts) // 2] if amounts else None,
                        "max": amounts[-1] if amounts else None},
        "with_hits": sum(1 for r in x if r["hits"]), "alerts": sum(1 for r in x if r["alert"]),
        "worst": Counter(r["worst"] or "none" for r in x),
        "rules": dict(Counter(h["rule"] for r in x for h in r["hits"]).most_common()),
        "limitations": [
            "x402 = USDC inflow whose fee payer is a facilitator in the snapshot (Oct 2026 lists); new facilitators "
            "are missed and their payments end up in the x402: false control set.",
            f"At most {args.per_address} transactions per address, newest first: busy addresses are cut short.",
            "Phishing tags in the snapshot come from Blockscout and cover Base only.",
        ],
    }
    (OUT / "solana_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("usdc_inflows", "x402_payments", "distinct", "alerts", "rules",
                                              "requests", "credits_estimate", "seconds")}, indent=2))


if __name__ == "__main__":
    main()
