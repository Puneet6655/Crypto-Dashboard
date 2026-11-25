"""Check an answer for addresses, transaction IDs and amounts that are not in the facts the model was given.

Small models sometimes write a plausible-looking address or amount that is not in the data. This check
does not judge the wording; it only lists concrete identifiers and amounts it cannot find, so the
dashboard can warn the analyst next to the answer.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

# Full identifiers only. Shortened ones such as "bc1qxy…0wlh" are not checked.
_PATTERNS = {
    "bitcoin address": re.compile(r"\b(?:bc1[ac-hj-np-z02-9]{25,87}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b"),
    "evm address": re.compile(r"\b0x[0-9a-fA-F]{40}\b"),
    "transaction id": re.compile(r"\b(?:0x)?[0-9a-fA-F]{64}\b"),
}
_AMOUNT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(BTC|sats?|satoshis|ETH|SOL|USDC)\b", re.IGNORECASE)
SATS_PER_BTC = Decimal(100_000_000)


def _numbers(text: str) -> set[Decimal]:
    found = set()
    for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        try:
            found.add(Decimal(raw.replace(",", "")).normalize())
        except InvalidOperation:
            pass
    return found


def _close(amount: Decimal, numbers: set[Decimal]) -> bool:
    """True if some number in the facts rounds to this amount (so "0.12 BTC" matches 0.1234 BTC)."""
    if amount.normalize() in numbers:
        return True
    step = Decimal(1).scaleb(min(0, amount.as_tuple().exponent))
    for number in numbers:
        try:
            if number.quantize(step) == amount.quantize(step):
                return True
        except InvalidOperation:
            continue
    return False


def check_answer(answer: str, facts: list) -> dict:
    """{"unverified": [{"kind", "value"}...], "checked": n} for the answer against every fact given."""
    corpus = "\n".join(f if isinstance(f, str) else json.dumps(f, default=str) for f in facts)
    corpus_lower = corpus.lower()
    numbers = _numbers(corpus)
    unverified, checked, seen = [], 0, set()

    for kind, pattern in _PATTERNS.items():
        for match in pattern.finditer(answer):
            value = match.group(0)
            if value in seen:
                continue
            seen.add(value)
            checked += 1
            if value.lower() not in corpus_lower:
                unverified.append({"kind": kind, "value": value})

    for match in _AMOUNT.finditer(answer):
        raw, unit = match.group(1), match.group(2).lower()
        try:
            amount = Decimal(raw.replace(",", ""))
        except InvalidOperation:
            continue
        if amount == 0 or match.group(0) in seen:
            continue
        seen.add(match.group(0))
        checked += 1
        candidates = [amount]
        if unit == "btc":
            candidates.append(amount * SATS_PER_BTC)
        elif unit.startswith("sat"):
            candidates.append(amount / SATS_PER_BTC)
        if not any(_close(c, numbers) for c in candidates):
            unverified.append({"kind": "amount", "value": match.group(0)})

    return {"unverified": unverified, "checked": checked}
