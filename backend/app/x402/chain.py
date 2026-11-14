"""Reading x402 payments on Base from free sources.

Order of sources:
  1. Blockscout's Base explorer API. With BLOCKSCOUT_API_KEY it uses the PRO API (api.blockscout.com/8453/...);
     without a key the public explorer (base.blockscout.com), which needs browser-like headers and is rate limited.
     This is how x402analysis finds clients and funders, and it costs no Alchemy compute units.
  2. Alchemy (ALCHEMY_API_KEY + ALCHEMY_ENABLED) as a backup: eth_getTransactionByHash + receipt (about 40 CU)
     for a transaction, alchemy_getAssetTransfers on USDC (120 CU) for an address.

An x402 payment on Base is a USDC transfer settled with transferWithAuthorization (EIP-3009): the facilitator sends
the transaction and pays the gas, so the transaction's sender is what marks it as x402 (see app/x402/snapshot.py).
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone

import httpx

from app.x402.rules import Payment
from app.x402.snapshot import USDC_BASE, Snapshot, norm

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
AUTHORIZATION_USED_TOPIC = "0x98de503528ee59b575ef0c0a2576a82497bfc029a5685b209e9ec333479b10a5"
GASLESS_METHODS = ("transferWithAuthorization", "receiveWithAuthorization")
USDC_DECIMALS = 6
PUBLIC_EXPLORER = "https://base.blockscout.com"
PRO_API = "https://api.blockscout.com/8453"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130 Safari/537.36")


class ChainError(Exception):
    pass


def _utc(value) -> str | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    return str(value).replace("Z", "+00:00")


def _int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value, 16) if isinstance(value, str) and value.startswith("0x") else int(value)
    except (TypeError, ValueError):
        return None


def _usdc(raw) -> float | None:
    try:
        return int(raw) / 10**USDC_DECIMALS
    except (TypeError, ValueError):
        return None


class BaseX402Reader:
    """Looks up USDC transfers on Base and classifies them as x402 payments using the snapshot's facilitators."""

    def __init__(self, snapshot: Snapshot, blockscout_key: str | None = None, alchemy_key: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, timeout: float = 20.0,
                 min_interval: float = 0.25):
        self.snap = snapshot
        self.blockscout_key = blockscout_key
        self.alchemy_key = alchemy_key
        self.transport = transport
        self.timeout = timeout
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = asyncio.Lock()
        self.calls = {"blockscout": 0, "alchemy": 0}
        self.last_source: str | None = None

    @classmethod
    def from_env(cls, snapshot: Snapshot) -> "BaseX402Reader":
        from app.config import get_settings

        s = get_settings()
        alchemy = s.alchemy_api_key if s.alchemy_enabled else None
        return cls(snapshot, os.getenv("BLOCKSCOUT_API_KEY") or None, alchemy)

    # ---- HTTP -------------------------------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, transport=self.transport, follow_redirects=True)

    async def _pace(self) -> None:
        async with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()

    def explorer_label(self) -> str:
        return "Blockscout PRO API (Base)" if self.blockscout_key else "Blockscout Base explorer (public)"

    def explorer_url(self, path: str) -> str:
        return f"{PRO_API}/api/v2{path}" if self.blockscout_key else f"{PUBLIC_EXPLORER}/api/v2{path}"

    async def _blockscout(self, path: str, params: dict | None = None) -> dict:
        await self._pace()
        query = dict(params or {})
        headers = {"Accept": "application/json"}
        if self.blockscout_key:
            query["apikey"] = self.blockscout_key
        else:
            headers.update({"User-Agent": BROWSER_UA, "Referer": PUBLIC_EXPLORER + "/"})
        self.calls["blockscout"] += 1
        async with self._client() as client:
            r = await client.get(self.explorer_url(path), params=query, headers=headers)
        if r.status_code == 404:
            raise ChainError("Blockscout does not know that transaction or address yet.")
        if r.status_code >= 400:
            raise ChainError(f"Blockscout answered HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError:
            raise ChainError("Blockscout did not answer with JSON (possibly its bot check).") from None
        if not isinstance(data, dict):
            raise ChainError("Unexpected answer from Blockscout.")
        return data

    async def _alchemy(self, method: str, params: list) -> object:
        if not self.alchemy_key:
            raise ChainError("Alchemy is not configured.")
        await self._pace()
        self.calls["alchemy"] += 1
        async with self._client() as client:
            r = await client.post(f"https://base-mainnet.g.alchemy.com/v2/{self.alchemy_key}",
                                  json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if r.status_code >= 400:
            raise ChainError(f"Alchemy answered HTTP {r.status_code}")
        body = r.json()
        if body.get("error"):
            raise ChainError(f"Alchemy: {body['error'].get('message')}")
        return body.get("result")

    # ---- classification ----------------------------------------------------------------------

    def _payment(self, tx: str, sender: str | None, payer: str, pay_to: str, raw_value, at, block,
                 method: str | None, gasless: bool | None = None) -> Payment:
        method = method or None
        is_gasless = gasless if gasless is not None else bool(method and method in GASLESS_METHODS)
        return Payment(chain="base", tx=tx.lower(), payer=norm(payer), pay_to=norm(pay_to), amount=_usdc(raw_value),
                       facilitator=norm(sender) if sender else None, at=_utc(at), block=_int(block), method=method,
                       gasless=is_gasless)

    def is_x402(self, p: Payment) -> bool:
        """Known facilitator sent it; or a gasless USDC transfer to a known x402 server."""
        if p.facilitator and self.snap.facilitator(p.facilitator):
            return True
        return bool(p.gasless and self.snap.server(p.pay_to))

    # ---- one transaction ---------------------------------------------------------------------

    async def transaction(self, tx_hash: str) -> dict:
        """{'tx', 'sender', 'payments': [Payment], 'source', 'source_url'} for one Base transaction."""
        tx_hash = tx_hash.strip().lower()
        errors = []
        try:
            return await self._transaction_blockscout(tx_hash)
        except (ChainError, httpx.HTTPError) as exc:
            errors.append(f"Blockscout: {exc}")
        if self.alchemy_key:
            try:
                return await self._transaction_alchemy(tx_hash)
            except (ChainError, httpx.HTTPError) as exc:
                errors.append(f"Alchemy: {exc}")
        raise ChainError("; ".join(errors))

    async def _transaction_blockscout(self, tx_hash: str) -> dict:
        tx = await self._blockscout(f"/transactions/{tx_hash}")
        sender = (tx.get("from") or {}).get("hash")
        method = tx.get("method") or ((tx.get("decoded_input") or {}).get("method_call") or "").split("(")[0] or None
        transfers = tx.get("token_transfers")
        if transfers is None or tx.get("token_transfers_overflow"):
            transfers = (await self._blockscout(f"/transactions/{tx_hash}/token-transfers")).get("items") or []
        payments = []
        for t in transfers:
            if ((t.get("token") or {}).get("address_hash") or (t.get("token") or {}).get("address") or "").lower() != USDC_BASE:
                continue
            payments.append(self._payment(tx_hash, sender, (t.get("from") or {}).get("hash") or "",
                                          (t.get("to") or {}).get("hash") or "", (t.get("total") or {}).get("value"),
                                          tx.get("timestamp"), tx.get("block_number") or tx.get("block"),
                                          t.get("method") or method))
        self.last_source = self.explorer_label()
        return {"tx": tx_hash, "sender": norm(sender or ""), "method": method, "status": tx.get("status"),
                "payments": payments, "source": self.explorer_label(),
                "source_url": f"{PUBLIC_EXPLORER}/tx/{tx_hash}"}

    async def _transaction_alchemy(self, tx_hash: str) -> dict:
        tx = await self._alchemy("eth_getTransactionByHash", [tx_hash])
        if not tx:
            raise ChainError("Alchemy does not know that transaction.")
        receipt = await self._alchemy("eth_getTransactionReceipt", [tx_hash]) or {}
        logs = receipt.get("logs") or []
        gasless = any((log.get("topics") or [""])[0] == AUTHORIZATION_USED_TOPIC
                      and (log.get("address") or "").lower() == USDC_BASE for log in logs)
        payments = []
        for log in logs:
            topics = log.get("topics") or []
            if (log.get("address") or "").lower() != USDC_BASE or not topics or topics[0] != TRANSFER_TOPIC or len(topics) < 3:
                continue
            payer, pay_to = "0x" + topics[1][-40:], "0x" + topics[2][-40:]
            payments.append(self._payment(tx_hash, tx.get("from"), payer, pay_to, _int(log.get("data")),
                                          None, tx.get("blockNumber"),
                                          "transferWithAuthorization" if gasless else None, gasless))
        self.last_source = "Alchemy (Base)"
        return {"tx": tx_hash, "sender": norm(tx.get("from") or ""), "method": None,
                "status": "ok" if receipt.get("status") == "0x1" else receipt.get("status"),
                "payments": payments, "source": "Alchemy (Base)", "source_url": f"{PUBLIC_EXPLORER}/tx/{tx_hash}"}

    # ---- one address -------------------------------------------------------------------------

    async def address_transfers(self, address: str, direction: str | None = None, limit: int = 50) -> dict:
        """Recent USDC transfers of one address, newest first: {'payments', 'source', 'source_url', 'senders_checked'}.
        direction: 'to' (received), 'from' (sent) or None (both). Transaction senders (to tell x402 apart) are
        looked up for gasless transfers only, at most 10 per call."""
        address = norm(address)
        errors = []
        try:
            out = await self._address_blockscout(address, direction, limit)
        except (ChainError, httpx.HTTPError) as exc:
            errors.append(f"Blockscout: {exc}")
            out = None
        if out is None and self.alchemy_key:
            try:
                out = await self._address_alchemy(address, direction, limit)
            except (ChainError, httpx.HTTPError) as exc:
                errors.append(f"Alchemy: {exc}")
        if out is None:
            raise ChainError("; ".join(errors) or "No source for Base transfers is configured.")
        await self._fill_senders(out["payments"])
        return out

    async def _address_blockscout(self, address: str, direction: str | None, limit: int) -> dict:
        params = {"token": USDC_BASE}
        if direction in ("to", "from"):
            params["filter"] = direction
        data = await self._blockscout(f"/addresses/{address}/token-transfers", params)
        payments = []
        for t in (data.get("items") or [])[:limit]:
            payments.append(self._payment(t.get("transaction_hash") or t.get("tx_hash") or "", None,
                                          (t.get("from") or {}).get("hash") or "", (t.get("to") or {}).get("hash") or "",
                                          (t.get("total") or {}).get("value"), t.get("timestamp"),
                                          t.get("block_number"), t.get("method")))
        return {"payments": payments, "source": self.explorer_label(),
                "source_url": f"{PUBLIC_EXPLORER}/address/{address}?tab=token_transfers"}

    async def _address_alchemy(self, address: str, direction: str | None, limit: int) -> dict:
        payments = []
        for key in (["toAddress"] if direction == "to" else ["fromAddress"] if direction == "from"
                    else ["toAddress", "fromAddress"]):
            result = await self._alchemy("alchemy_getAssetTransfers", [{
                key: address, "category": ["erc20"], "contractAddresses": [USDC_BASE], "order": "desc",
                "maxCount": hex(min(limit, 100)), "withMetadata": True, "excludeZeroValue": True}]) or {}
            for t in result.get("transfers", []):
                raw = (t.get("rawContract") or {}).get("value")
                payments.append(self._payment(t.get("hash") or "", None, t.get("from") or "", t.get("to") or "",
                                              _int(raw), (t.get("metadata") or {}).get("blockTimestamp"),
                                              t.get("blockNum"), None))
        payments.sort(key=lambda p: p.block or 0, reverse=True)
        return {"payments": payments[:limit], "source": "Alchemy (Base)",
                "source_url": f"{PUBLIC_EXPLORER}/address/{address}?tab=token_transfers"}

    async def _fill_senders(self, payments: list[Payment], max_lookups: int = 10) -> None:
        """Find who sent each transaction, so a payment can be tied to a facilitator. Payments to a known x402
        server or with a gasless method come first; the rest stay without a sender (shown as plain USDC)."""
        wanted = [p for p in payments if p.facilitator is None and (p.gasless or self.snap.server(p.pay_to))]
        seen: dict[str, str | None] = {}
        for p in wanted:
            if p.tx in seen:
                p.facilitator = seen[p.tx]
                continue
            if len(seen) >= max_lookups:
                break
            try:
                if self.blockscout_key or not self.alchemy_key:
                    tx = await self._blockscout(f"/transactions/{p.tx}")
                    sender = (tx.get("from") or {}).get("hash")
                else:
                    tx = await self._alchemy("eth_getTransactionByHash", [p.tx]) or {}
                    sender = tx.get("from")
            except (ChainError, httpx.HTTPError):
                sender = None
            seen[p.tx] = norm(sender) if sender else None
            p.facilitator = seen[p.tx]
