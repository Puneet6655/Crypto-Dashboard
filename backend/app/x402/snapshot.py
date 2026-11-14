"""The x402 snapshot in backend/data/x402/ (built by training/x402_snapshot.py).

It comes from Peter Robinson's x402analysis dataset (MIT, https://github.com/drinkcoffee/x402analysis) and
x402scan's facilitator list (MIT, https://github.com/Merit-Systems/x402scan). Every lookup here is local: no
network calls. A missing file only switches that check off; the dashboard says so instead of failing.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from app.config import BACKEND_DIR

USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
USDC_SOLANA = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOURCE_URL = "https://github.com/drinkcoffee/x402analysis"
FACILITATOR_LIST_URL = "https://github.com/Merit-Systems/x402scan"


def folder() -> Path:
    return Path(os.getenv("X402_DATA_DIR") or BACKEND_DIR / "data" / "x402")


def norm(address: str) -> str:
    address = (address or "").strip()
    return address.lower() if address.startswith("0x") else address


class Snapshot:
    def __init__(self, path: Path):
        self.path = path

        def load(name, default):
            try:
                return json.loads((path / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return default

        self.meta: dict = load("meta.json", {})
        self.facilitators: dict[str, dict] = {f["address"]: f for f in load("facilitators.json", [])}
        self.servers: dict[str, dict] = load("servers.json", {})
        self.phishing: dict[str, dict] = {p["address"]: p for p in load("phishing.json", [])}
        self.phishing_funded: dict[str, dict] = load("phishing_funded.json", {})
        self.taint: dict[str, dict] = load("taint.json", {})
        self.clients: dict[str, list] = load("clients.json", {})

    @property
    def loaded(self) -> bool:
        return bool(self.meta)

    def facilitator(self, address: str) -> dict | None:
        return self.facilitators.get(norm(address))

    def facilitator_addresses(self, chain: str = "base", include_deprecated: bool = False) -> list[str]:
        return [a for a, f in self.facilitators.items()
                if f["chain"] == chain and (include_deprecated or not f.get("deprecated"))]

    def server(self, address: str) -> dict | None:
        return self.servers.get(norm(address))

    def client(self, address: str) -> dict | None:
        row = self.clients.get(norm(address))
        if row is None:
            return None
        transfers, usdc, servers, top = row
        return {"transfers": transfers, "usdc": usdc, "servers_paid": servers, "top_server": top}

    def role_of(self, address: str) -> list[str]:
        a = norm(address)
        roles = []
        if a in self.facilitators:
            roles.append("facilitator")
        if a in self.servers:
            roles.append("server")
        if a in self.clients:
            roles.append("client")
        if a in self.phishing and "funder" in (self.phishing[a].get("kinds") or []):
            roles.append("funder")
        return roles

    def risky_servers(self, limit: int = 50) -> list[dict]:
        """Payment addresses worth watching: those that took US$10 or more from phishing-linked clients (most such
        money first), interleaved with busy servers selling high-risk services (most USDC received first)."""
        rows = []
        for address, s in self.servers.items():
            t = self.taint.get(address) or {}
            if not (s.get("risk") == "high" or (t.get("from_phishing") or 0) >= 10):
                continue
            rows.append({"address": address, "chain": s["chain"], "servers": [x["name"] for x in s["servers"]],
                         "risk": s.get("risk"), "categories": sorted(s["categories"], key=lambda c: -s["categories"][c])[:6],
                         "usdc_received": t.get("usdc_received"), "from_phishing": t.get("from_phishing"),
                         "from_phishing_percent": t.get("from_phishing_percent"), "clients": t.get("clients"),
                         "phishing_funded": t.get("phishing_funded", False), "countries": s.get("countries", [])})
        tainted = sorted((r for r in rows if (r["from_phishing"] or 0) >= 10), key=lambda r: -r["from_phishing"])
        risky = sorted((r for r in rows if (r["from_phishing"] or 0) < 10), key=lambda r: -(r["usdc_received"] or 0))
        mixed = [r for pair in zip(tainted, risky) for r in pair] + tainted[len(risky):] + risky[len(tainted):]
        return mixed[:limit]

    def summary(self) -> dict:
        return {"loaded": self.loaded, "folder": str(self.path), "built_at": self.meta.get("built_at"),
                "gathered": self.meta.get("gathered", {}), "counts": self.meta.get("counts", {}),
                "sources": self.meta.get("sources", [])}


@lru_cache(maxsize=1)
def get_snapshot() -> Snapshot:
    return Snapshot(folder())


def reload_snapshot() -> Snapshot:
    get_snapshot.cache_clear()
    return get_snapshot()
