"""Shared pieces for the Ethereum-style (EVM) adapters: Etherscan and Alchemy.

* Small converters for hex and decimal numbers, timestamps, addresses and token names.
* from_rpc(): one JSON-RPC transaction + receipt -> EthTransaction. Etherscan's proxy calls and
  Alchemy both return plain JSON-RPC objects, so a transaction looks the same from either.
* Secret redaction for httpx's request log lines (Etherscan puts the key in the query string,
  Alchemy in the URL path).
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone

from app.schemas.bitcoin import Provenance
from app.schemas.ethereum import EthTokenTransfer, EthTransaction

MAX_RECORDS_PER_CALL = 1000  # Etherscan list calls and Alchemy transfer calls both stop at 1,000
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TTL_RECENT = 30.0
TTL_CONFIRMED_TX = 86_400.0

_KEY_IN_QUERY = re.compile(r"(apikey=)[^&\s\"']+", re.IGNORECASE)
_SECRETS: set[str] = set()


def register_secret(value: str | None) -> None:
    """Hide this exact string (an API key) in every httpx log line from now on."""
    if value and len(value) >= 8:
        _SECRETS.add(value)


def redact(text: str) -> str:
    text = _KEY_IN_QUERY.sub(r"\1REDACTED", text)
    for secret in _SECRETS:
        text = text.replace(secret, "REDACTED")
    return text


class _RedactSecrets(logging.Filter):
    """httpx logs every request URL at INFO level; remove API keys from those lines."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
        return True


_httpx_log = logging.getLogger("httpx")
if not any(isinstance(f, _RedactSecrets) for f in _httpx_log.filters):
    _httpx_log.addFilter(_RedactSecrets())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_int(value, base: int = 10) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value, base) if isinstance(value, str) else int(value)
    except ValueError:
        return None


def from_unix(seconds: int | None) -> datetime | None:
    return datetime.fromtimestamp(seconds, tz=timezone.utc) if seconds else None


def plain_text(value: str | None) -> str | None:
    """Token names and symbols can arrive HTML-escaped (e.g. '&lt;3'); return them as plain text."""
    return html.unescape(value) if value else None


def lower(value: str | None) -> str | None:
    return value.lower() if value else None


def from_rpc(tx: dict, receipt: dict | None, block_number: int | None, block_time: datetime | None,
             provenance: Provenance, chain: str, network: str, chain_id: int) -> EthTransaction:
    """A JSON-RPC transaction and receipt as an EthTransaction, with the ERC-20 transfers it emitted."""
    tx_hash = tx["hash"].lower()
    status, fee, created, transfers = "pending", None, None, []
    if receipt:
        status = "success" if receipt.get("status") == "0x1" else "failed"
        gas_used = to_int(receipt.get("gasUsed"), 16)
        price = to_int(receipt.get("effectiveGasPrice"), 16) or to_int(tx.get("gasPrice"), 16)
        fee = str(gas_used * price) if gas_used is not None and price is not None else None
        created = lower(receipt.get("contractAddress"))
        for entry in receipt.get("logs") or []:
            topics = entry.get("topics") or []
            # ERC-20 Transfer(from, to, value): three topics, amount in data. (ERC-721 has four.)
            if len(topics) == 3 and topics[0].lower() == TRANSFER_TOPIC and entry.get("data", "0x") != "0x":
                transfers.append(EthTokenTransfer(
                    tx_hash=tx_hash, block_number=block_number, block_time=block_time,
                    token_contract=entry["address"].lower(), token_symbol=None, token_decimals=None,
                    from_address="0x" + topics[1][-40:].lower(), to_address="0x" + topics[2][-40:].lower(),
                    value_raw=str(int(entry["data"], 16)),
                ))
    data = tx.get("input") or "0x"
    return EthTransaction(
        entity_id=f"{chain}:{network}:tx:{tx_hash}", network=network, chain_id=chain_id,
        tx_hash=tx_hash, status=status, block_number=block_number, block_time=block_time,
        from_address=tx["from"].lower(), to_address=lower(tx.get("to")), contract_created=created,
        value_wei=str(to_int(tx.get("value"), 16) or 0), fee_wei=fee,
        method=data[:10] if len(data) >= 10 else None, token_transfers=transfers, provenance=provenance,
    )
