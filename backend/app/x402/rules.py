"""Explainable rules for x402 payments and addresses. Each rule that fires gives one hit with its evidence.

The rules follow the heuristics in Peter Robinson's x402analysis (phishing funders with a dust filter,
time-bounded labels, taint share, unusual service categories) plus a few for live payments (odd amounts,
unlisted facilitators). No rule is a verdict: a hit is a reason to look closer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from app.x402.snapshot import SOURCE_URL, Snapshot, norm

SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1, "info": 0}
TAINT_MEDIUM = 5.0   # % of a server's USDC from phishing-linked clients
TAINT_HIGH = 15.0
TAINT_MIN_USD = 10.0  # ignore percentages built on a few cents
SHARED_PAYTO = 5      # payment address used by this many servers or more
BIG_PAYMENT = 1000.0  # USDC
ODD_MIN = 100.0       # an amount must be at least this ...
ODD_FACTOR = 20.0     # ... and this many times the service's highest listed price to count as odd
CASH_OUT_MIN = 50.0   # a gift card or eSIM purchase at least this big is worth a look
CASH_OUT_PREFIXES = ("*giftcard", "*esim", "*prepay-credit", "*banking-transfer")
# Hits about the payment itself. Server-level hits (taint share, categories) are context: they would fire on
# every payment to that server, so on their own they do not raise a live alert.
TRIGGERS = {"phishing_address", "phishing_funded", "sanctions_or_scam_list", "large_payment", "odd_amount",
            "cash_out_purchase"}


@dataclass
class Hit:
    rule: str
    severity: str
    title: str
    detail: str
    source: str = SOURCE_URL
    address: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Payment:
    """One x402 payment (or a USDC transfer that may be one)."""
    chain: str
    tx: str
    payer: str
    pay_to: str
    amount: float | None
    facilitator: str | None = None  # the address that sent the transaction, when known
    at: str | None = None           # ISO time
    block: int | None = None
    method: str | None = None
    gasless: bool = False            # settled with transferWithAuthorization (EIP-3009)

    def as_dict(self) -> dict:
        return asdict(self)


def alert_worthy(hits: list[Hit], min_severity: str = "medium") -> bool:
    floor = SEVERITY_ORDER[min_severity]
    return any(h.rule in TRIGGERS and SEVERITY_ORDER[h.severity] >= floor for h in hits)


def worst(hits: list[Hit]) -> str | None:
    return max((h.severity for h in hits), key=lambda s: SEVERITY_ORDER[s], default=None)


def _short(a: str) -> str:
    return a if len(a) <= 14 else f"{a[:8]}…{a[-4:]}"


def _phishing_hits(snap: Snapshot, address: str, role: str, at: str | None) -> list[Hit]:
    hits = []
    a = norm(address)
    p = snap.phishing.get(a)
    if p is not None:
        since = p.get("from")
        if since and at and at < since:
            hits.append(Hit("phishing_label_later", "info", f"The {role} was tagged as phishing later",
                            f"{p.get('name') or _short(a)} is tagged '{p.get('tags')}' from {since}; this activity is "
                            "before that time.", address=a))
        else:
            hits.append(Hit("phishing_address", "high", f"The {role} is tagged as phishing",
                            f"{p.get('name') or _short(a)}: '{p.get('tags')}' in Blockscout's address metadata "
                            f"(as gathered by x402analysis){'; tagged from ' + since if since else ''}. "
                            + (p.get("notes") or ""), address=a))
    f = snap.phishing_funded.get(a)
    if f is not None:
        hits.append(Hit("phishing_funded", "high", f"The {role} was funded by a phishing-tagged address",
                        f"{f.get('funder_name') or _short(f['funder'])} sent it about US${f['usd']:,.2f}"
                        f"{' from ' + f['first'][:10] if f.get('first') else ''} (dust under US$0.01 ignored).",
                        address=a))
    return hits


def _sanction_hits(index, chain: str, address: str, role: str) -> list[Hit]:
    if index is None:
        return []
    sources = ["ofac_sol"] if chain == "solana" else ["ofac_eth", "cryptoscamdb_blacklist"]
    hits = []
    try:
        found = index.lookup(address, sources)
    except Exception:  # an index without these lists must not stop the other rules
        return []
    for h in found:
        sev = "high"
        hits.append(Hit("sanctions_or_scam_list", sev, f"The {role} is on the {h.title}",
                        f"{_short(address)} is listed as {h.label}" + (f" ({h.family})" if h.family else "") + ".",
                        source=h.title, address=norm(address)))
    return hits


def server_hits(snap: Snapshot, pay_to: str) -> list[Hit]:
    hits = []
    a = norm(pay_to)
    s = snap.server(a)
    t = snap.taint.get(a)
    if t and t.get("from_phishing", 0) >= TAINT_MIN_USD:
        pct = t["from_phishing_percent"]
        if pct >= TAINT_MEDIUM:
            hits.append(Hit("tainted_server", "high" if pct >= TAINT_HIGH else "medium",
                            f"{pct:.1f}% of this server's USDC came from phishing-linked clients",
                            f"US${t['from_phishing']:,.2f} of US${t['usdc_received']:,.2f} received, from "
                            f"{t['clients']:,} clients (x402analysis phishing funds flow).", address=a))
    if t and t.get("phishing_funded"):
        hits.append(Hit("server_phishing_funded", "high", "The server's payment address was funded by a phishing funder",
                        "x402analysis found a phishing-tagged address among this payment address's funders.", address=a))
    if s:
        risky = [c for c in s["categories"] if c.startswith("*")]
        if s.get("risk") == "high":
            hits.append(Hit("risky_service", "medium", "The server sells services in a high-risk category",
                            "Categories: " + ", ".join(c.lstrip("*") for c in risky[:6]) +
                            ". Gift cards and eSIMs are cash-out routes; others expose or attack other people.",
                            address=a))
        elif s.get("risk") == "medium":
            hits.append(Hit("unusual_service", "low", "The server sells unusual (dual-use) services",
                            "Categories: " + ", ".join(c.lstrip("*") for c in risky[:6]) + ".", address=a))
        if s.get("shared_by", 1) >= SHARED_PAYTO:
            hits.append(Hit("shared_payto", "low", f"This payment address is shared by {s['shared_by']} servers",
                            "One wallet collecting for many unrelated servers is a clustering signal.", address=a))
    return hits


def check_payment(snap: Snapshot, p: Payment, index=None) -> list[Hit]:
    hits: list[Hit] = []
    hits += _phishing_hits(snap, p.payer, "payer", p.at)
    hits += _phishing_hits(snap, p.pay_to, "payee", p.at)
    hits += _sanction_hits(index, p.chain, p.payer, "payer")
    hits += _sanction_hits(index, p.chain, p.pay_to, "payee")
    if p.facilitator:
        hits += _sanction_hits(index, p.chain, p.facilitator, "facilitator")
    hits += server_hits(snap, p.pay_to)

    s = snap.server(p.pay_to)
    if p.amount is not None:
        if p.amount >= BIG_PAYMENT:
            hits.append(Hit("large_payment", "medium", f"Large payment: US${p.amount:,.2f}",
                            "Typical x402 calls cost about US$0.01 (median across 28,000 priced services)."))
        elif s and s.get("max_price") and p.amount >= max(ODD_MIN, ODD_FACTOR * s["max_price"]):
            hits.append(Hit("odd_amount", "medium", f"US${p.amount:,.2f} is far above this server's listed prices",
                            f"Its services list at most US${s['max_price']:,.4f} a call."))
    if s and p.amount is not None and p.amount >= CASH_OUT_MIN and any(
            c.startswith(CASH_OUT_PREFIXES) for c in s["categories"]):
        hits.append(Hit("cash_out_purchase", "medium", f"US${p.amount:,.2f} spent at a gift card / eSIM seller",
                        "Buying gift cards, eSIMs or prepaid credit with crypto is a common cash-out route "
                        "(Bitrefill took US$14M of x402 volume on Solana in x402analysis's data)."))
    if p.gasless and p.facilitator and not snap.facilitator(p.facilitator):
        hits.append(Hit("unknown_facilitator", "low", "Gasless USDC payment sent by an unlisted address",
                        f"{_short(p.facilitator)} settled a transferWithAuthorization but is on neither the "
                        "x402analysis nor the x402scan facilitator list: possibly a new facilitator.",
                        source="https://github.com/Merit-Systems/x402scan"))
    if s is None and p.facilitator and snap.facilitator(p.facilitator):
        hits.append(Hit("unknown_server", "info", "Paid to an address that is not in the x402 server snapshot",
                        "The server may be new since the snapshot, or not listed on x402scan or the CDP Bazaar."))
    return hits


def check_address(snap: Snapshot, address: str, chain: str, index=None) -> list[Hit]:
    hits = _phishing_hits(snap, address, "address", None)
    hits += _sanction_hits(index, chain, address, "address")
    hits += server_hits(snap, address)
    return hits
