"""Build the compact x402 snapshot the dashboard reads (backend/data/x402/) from two MIT-licensed sources:

  * Peter Robinson's x402analysis (https://github.com/drinkcoffee/x402analysis), gather/dataset/
  * x402scan's facilitator list (https://github.com/Merit-Systems/x402scan), packages/external/facilitators

Usage (from backend/):
    python -m training.x402_snapshot --x402analysis ../../x402analysis --x402scan ../../x402scan

Both arguments are local clones. The output is small (a few MB) so it can live in git and ship with the server.
Nothing is fetched from the network here, and no model is trained on this data.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "x402"
USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
USDC_SOLANA = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
ETH_USD = 2500.0  # same assumption as x402analysis script 35, only used for the dust filter
MIN_FUNDING_USD = 0.01  # less than this in all = address poisoning, not funding

# Categories x402analysis marks with "*" (unusual). High = cash-out, attack tooling, exposure of other people's
# data, gambling, deepfakes; the rest of the "*" list is medium (dual use: password generators, image generation).
HIGH_RISK_PREFIXES = ("*giftcard", "*esim-purchase", "*attack", "*vpn-ip-hopping", "*password-exposure",
                      "*secret-exposure", "*image-faceswap", "*gambling", "*banking-transfer", "*prepay-credit",
                      "*pii-detect", "*password-scan", "*secret-scan", "*password-login")


def risk_tier(category: str | None) -> str | None:
    if not category or not category.startswith("*"):
        return None
    return "high" if category.startswith(HIGH_RISK_PREFIXES) else "medium"


def norm(address: str) -> str:
    return address.lower() if address.startswith("0x") else address


_BASE58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def chain_of(address: str) -> str:
    if address.startswith("0x"):
        return "base"
    return "solana" if _BASE58.match(address) else "other"


def parse_price(text: str | None) -> tuple[float | None, str | None]:
    """'3000 0x8335... on eip155:8453' -> (0.003, 'base') for USDC; other tokens -> (None, chain)."""
    if not text:
        return None, None
    parts = text.split()
    chain = "base" if "eip155:8453" in text else ("solana" if "solana" in text.lower() else None)
    try:
        units = int(parts[0])
    except (ValueError, IndexError):
        return None, chain
    token = parts[1] if len(parts) > 1 else ""
    if norm(token) in (USDC_BASE, USDC_SOLANA):
        return units / 1e6, chain
    return None, chain


def build_facilitators(peter: Path, scan: Path | None) -> list[dict]:
    merged: dict[str, dict] = {}
    data = json.loads((peter / "facilitators.json").read_text())
    for f in data["facilitators"]:
        for a in f["addresses"]:
            e = merged.setdefault(norm(a), {"address": norm(a), "chain": chain_of(a), "names": [], "sources": [],
                                            "deprecated": False})
            if f["name"] not in e["names"]:
                e["names"].append(f["name"])
            if "x402analysis" not in e["sources"]:
                e["sources"].append("x402analysis")
    if scan is not None:
        folder = scan / "packages" / "external" / "facilitators" / "src" / "facilitators"
        for path in sorted(folder.glob("*.ts")):
            text = path.read_text()
            m = re.search(r"name:\s*'([^']+)'", text)
            name = m.group(1) if m else path.stem
            for seg in re.split(r"\[Network\.", text)[1:]:
                network = seg.split("]", 1)[0].lower()
                if network not in ("base", "solana"):
                    continue
                for am in re.finditer(r"address:\s*'([^']+)'(?P<rest>[^}]*)", seg):
                    a = norm(am.group(1))
                    e = merged.setdefault(a, {"address": a, "chain": network, "names": [], "sources": [],
                                              "deprecated": False})
                    if name not in e["names"]:
                        e["names"].append(name)
                    if "x402scan" not in e["sources"]:
                        e["sources"].append("x402scan")
                    if "deprecated: true" in am.group("rest"):
                        e["deprecated"] = True
    return sorted(merged.values(), key=lambda e: (e["chain"], e["names"][0].lower(), e["address"]))


def build_servers(peter: Path) -> dict[str, dict]:
    """payTo address -> what is behind it."""
    data = json.loads((peter / "services-annotated.json").read_text())
    out: dict[str, dict] = {}
    for s in data["servers"]:
        cats = defaultdict(int)
        prices = []
        for sv in s.get("services", []):
            if sv.get("category"):
                cats[sv["category"]] += 1
            p, _ = parse_price(sv.get("price"))
            if p is not None:
                prices.append(p)
        country = None
        fp = (s.get("fingerprints") or {}).get(s.get("api_url") or "", {})
        if fp.get("location"):
            country = fp["location"].split(",")[-1].strip()
        for addr in (s.get("addresses") or {}):
            a = norm(addr)
            e = out.setdefault(a, {"chain": chain_of(addr), "servers": [], "categories": {}, "services": 0,
                                   "min_price": None, "max_price": None, "docs": False, "countries": []})
            e["servers"].append({"name": s.get("name"), "url": s.get("api_url")})
            e["services"] += len(s.get("services", []))
            for c, n in cats.items():
                e["categories"][c] = e["categories"].get(c, 0) + n
            if prices:
                e["min_price"] = min([p for p in (e["min_price"], min(prices)) if p is not None])
                e["max_price"] = max([p for p in (e["max_price"], max(prices)) if p is not None])
            e["docs"] = e["docs"] or bool(s.get("doc_url"))
            if country and country not in e["countries"]:
                e["countries"].append(country)
    for e in out.values():
        tiers = [risk_tier(c) for c in e["categories"]]
        e["risk"] = "high" if "high" in tiers else ("medium" if "medium" in tiers else None)
        e["shared_by"] = len(e["servers"])
        e["servers"] = e["servers"][:10]
    return out


def build_phishing(peter: Path) -> list[dict]:
    data = json.loads((peter / "metadata_base_phishing_only_annotated.json").read_text())
    rows = []
    for a in data["addresses"]:
        text = f"{a.get('name') or ''} {a.get('tags') or ''}".lower()
        if "phish" not in text:
            continue
        rows.append({"address": norm(a["address"]), "kinds": a.get("kinds", []), "name": a.get("name"),
                     "tags": a.get("tags"), "notes": a.get("notes"), "from": a.get("from")})
    return rows


def _usd(entry: dict) -> float:
    total = 0.0
    if entry.get("usdc"):
        total += float(entry["usdc"].get("amount") or 0)
    if entry.get("eth"):
        total += float(entry["eth"].get("amount") or 0) * ETH_USD
    return total


def build_phishing_funded(peter: Path, phishing: list[dict]) -> dict[str, dict]:
    bad = {p["address"]: p for p in phishing}
    out: dict[str, dict] = {}
    for part in sorted(peter.glob("funders.part*.json")):
        for f in json.loads(part.read_text())["funders"]:
            p = bad.get(norm(f["address"]))
            if p is None:
                continue
            since = p.get("from")
            for d in f.get("funded", []):
                if _usd(d) < MIN_FUNDING_USD:
                    continue
                last = max([x.get("last") or "" for x in (d.get("usdc") or {}, d.get("eth") or {})])
                if since and last and last < since:
                    continue
                firsts = [x.get("first") for x in (d.get("usdc") or {}, d.get("eth") or {}) if x.get("first")]
                a = norm(d["address"])
                prev = out.get(a)
                if prev is None or _usd(d) > prev["usd"]:
                    out[a] = {"funder": p["address"], "funder_name": p.get("name"), "kinds": d.get("kinds", []),
                              "names": d.get("names", [])[:3], "usd": round(_usd(d), 6),
                              "first": min(firsts) if firsts else None}
    return out


def build_taint(peter: Path) -> dict[str, dict]:
    out = {}
    with (peter / "phishing_funds_flow.csv").open(newline="") as fh:
        for r in csv.DictReader(fh):
            for addr in r["payment_addresses"].split(";"):
                addr = addr.strip()
                if not addr:
                    continue
                out[norm(addr)] = {"rank": int(r["rank"]), "usdc_received": float(r["usdc_received"]),
                                   "transfers": int(r["transfers"]), "clients": int(r["clients"]),
                                   "median_transfer": float(r["median_transfer"]),
                                   "from_phishing": float(r["from_phishing"]),
                                   "from_phishing_percent": float(r["from_phishing_percent"]),
                                   "phishing_funded": r["phishing_funded"] == "yes", "chain": r["chain"]}
    return out


def build_clients(peter: Path) -> dict[str, list]:
    """client -> [transfers, usdc, servers paid, top server]."""
    out = {}
    for name in ("clients.json", "clients_solana.json"):
        for c in json.loads((peter / name).read_text())["clients"]:
            servers = c.get("servers", [])
            transfers = sum(int(s.get("transfers") or 0) for s in servers)
            usdc = sum(float(s.get("usdc") or 0) for s in servers)
            top = max(servers, key=lambda s: float(s.get("usdc") or 0))["server"] if servers else None
            out[norm(c["address"])] = [transfers, round(usdc, 6), len(servers), top]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--x402analysis", required=True, type=Path, help="local clone of drinkcoffee/x402analysis")
    ap.add_argument("--x402scan", type=Path, help="local clone of Merit-Systems/x402scan (facilitator list)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    peter = args.x402analysis / "gather" / "dataset"
    args.out.mkdir(parents=True, exist_ok=True)

    facilitators = build_facilitators(peter, args.x402scan)
    servers = build_servers(peter)
    phishing = build_phishing(peter)
    funded = build_phishing_funded(peter, phishing)
    taint = build_taint(peter)
    clients = build_clients(peter)

    def gathered(name):
        try:
            return json.loads((peter / name).read_text()).get("gathered_at")
        except (OSError, ValueError):
            return None

    meta = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": [
            {"name": "x402analysis by Peter Robinson", "url": "https://github.com/drinkcoffee/x402analysis",
             "licence": "MIT", "files": "gather/dataset"},
            {"name": "x402scan facilitator list (Merit Systems)", "url": "https://github.com/Merit-Systems/x402scan",
             "licence": "MIT", "files": "packages/external/facilitators"},
        ],
        "gathered": {"facilitators": gathered("facilitators.json"), "services": gathered("services-annotated.json"),
                     "clients_base": gathered("clients.json"), "clients_solana": gathered("clients_solana.json"),
                     "phishing": gathered("metadata_base_phishing_only_annotated.json")},
        "counts": {"facilitator_addresses": len(facilitators), "payment_addresses": len(servers),
                   "risky_payment_addresses": sum(1 for s in servers.values() if s["risk"]),
                   "phishing_addresses": len(phishing), "phishing_funded_addresses": len(funded),
                   "tainted_payment_addresses": sum(1 for t in taint.values() if t["from_phishing"] > 0),
                   "clients": len(clients)},
        "high_risk_prefixes": list(HIGH_RISK_PREFIXES),
        "min_funding_usd": MIN_FUNDING_USD,
    }
    files = {"meta.json": meta, "facilitators.json": facilitators, "servers.json": servers,
             "phishing.json": phishing, "phishing_funded.json": funded, "taint.json": taint, "clients.json": clients}
    for name, value in files.items():
        (args.out / name).write_text(json.dumps(value, separators=(",", ":"), sort_keys=True))
    print(json.dumps(meta["counts"], indent=1))


if __name__ == "__main__":
    main()
