"""Checks on live data that the backup provider gives exactly the same answers as the primary.

For each transaction: mempool.space and blockstream.info must give the same TransactionSummary
(ignoring provenance). For each address: the wallet features from app/features/wallet_window.py
must be identical when every page comes from mempool.space, when every page comes from
blockstream.info, and when the pages alternate between them (as after a failover mid-lookup).
Confirmed address totals must match too; unconfirmed totals may differ, because each node sees
its own mempool.

Run from backend/ (about 100 requests, spaced out, a few minutes):

    ..\\.venv\\Scripts\\python.exe -m scripts.check_provider_parity
    ..\\.venv\\Scripts\\python.exe -m scripts.check_provider_parity <address> [<address> ...]

Exit code 0 when everything matches.
"""
import asyncio
import sys

from app.config import get_settings
from app.features.wallet_window import compute_wallet_features, fetch_window_transactions
from app.providers.base import BitcoinProvider, ProviderError
from app.providers.bitcoin_esplora import BlockstreamEsploraProvider
from app.providers.bitcoin_mempool import MempoolSpaceProvider

TXIDS = [
    "f4184fc596403b9d638783cf57adfe4c75c605f6356fbc91338530e9831e9e16",  # 2009, pay-to-pubkey in and out
    "b1fea52486ce0c62bb442b530a3f0132b826c74e473d1f2c220bfa78111c5082",  # coinbase of block 170
]
ADDRESSES = [
    "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",  # well-known example address, moderate history
    "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",  # genesis address: constant dust, truncated window
    "111iu559diy1cBDTP9cRhy8sYYcECvvrw",  # Elliptic++ illicit wallet
    "111112TykSw72ztDN2WJger4cynzWYC5w",  # Elliptic++ licit wallet
    "1143t96LASL6q6UMAJKQKd5BLkeV6Q19ys",  # Ransomwhere (Locky, 2016)
    "bc1q05aktddf9ce4p7hh3stgsf253m4vweu7nkhtmw",  # OFAC sanctioned
]
PAUSE_SECONDS = 0.5


class Paced(BitcoinProvider):
    """Spaces requests out and waits politely on a rate limit, so the check itself stays within limits."""

    def __init__(self, inner: BitcoinProvider):
        self.inner = inner
        self.name = inner.name
        self.requests = 0

    async def _do(self, method: str, *args):
        for attempt in range(4):
            await asyncio.sleep(PAUSE_SECONDS)
            self.requests += 1
            try:
                return await getattr(self.inner, method)(*args)
            except ProviderError as exc:
                if exc.status_code not in (429, 502, 503, 504) or attempt == 3:
                    raise
                print(f"    {self.name}: {exc.message}; waiting 30 s")
                await asyncio.sleep(30)

    async def get_address_summary(self, address):
        return await self._do("get_address_summary", address)

    async def get_transaction(self, txid):
        return await self._do("get_transaction", txid)

    async def get_address_transactions(self, address, after_txid=None):
        return await self._do("get_address_transactions", address, after_txid)

    async def health(self):
        return await self.inner.health()


class Alternating(BitcoinProvider):
    """Page 1 from the first provider, page 2 from the second, and so on."""

    name = "alternating"

    def __init__(self, first: BitcoinProvider, second: BitcoinProvider):
        self.providers = (first, second)
        self.calls = 0

    async def get_address_transactions(self, address, after_txid=None):
        provider = self.providers[self.calls % 2]
        self.calls += 1
        return await provider.get_address_transactions(address, after_txid)

    async def get_address_summary(self, address):
        raise NotImplementedError

    async def get_transaction(self, txid):
        raise NotImplementedError

    async def health(self):
        raise NotImplementedError


def _diff(a: dict, b: dict) -> list[str]:
    return [f"{k}: {a.get(k)!r} != {b.get(k)!r}" for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]


async def features(provider: BitcoinProvider, address: str) -> tuple[dict, int]:
    txs = await fetch_window_transactions(provider, address)
    return compute_wallet_features(address, txs), len(txs)


async def check_address(mempool: Paced, esplora: Paced, address: str) -> bool:
    a = await mempool.get_address_summary(address)
    b = await esplora.get_address_summary(address)
    summary_ok = a.confirmed == b.confirmed
    note = "" if a.unconfirmed == b.unconfirmed else " (unconfirmed totals differ: separate mempools)"

    for attempt in range(2):
        fm, n = await features(mempool, address)
        fe, _ = await features(esplora, address)
        fx, _ = await features(Alternating(mempool, esplora), address)
        problems = _diff(fm, fe) + [f"alternating {p}" for p in _diff(fm, fx)]
        if not problems or attempt == 1:
            break
        print("    mismatch; checking again in case a new block arrived meanwhile")
    ok = summary_ok and not problems
    print(f"  {'OK  ' if ok else 'DIFF'} {address}: {a.confirmed.tx_count} confirmed txs, {n} fetched for the window, "
          f"total_txs={fm['total_txs']:.0f}, truncated={fm['window_truncated']:.0f}{note}")
    if not summary_ok:
        print(f"       confirmed totals: {a.confirmed} vs {b.confirmed}")
    for p in problems[:10]:
        print(f"       {p}")
    return ok


async def main(addresses: list[str]) -> int:
    s = get_settings()
    mempool = Paced(MempoolSpaceProvider(s.btc_provider_base_url, s.btc_network, 20))
    esplora = Paced(BlockstreamEsploraProvider(s.btc_esplora_base_url, s.btc_network, 20))
    print(f"Comparing {mempool.name} ({s.btc_provider_base_url}) with {esplora.name} ({s.btc_esplora_base_url})")

    results = []
    print("Transactions:")
    for txid in TXIDS:
        a = (await mempool.get_transaction(txid)).model_dump(exclude={"provenance"})
        b = (await esplora.get_transaction(txid)).model_dump(exclude={"provenance"})
        problems = _diff(a, b)
        results.append(not problems)
        print(f"  {'OK  ' if not problems else 'DIFF'} {txid}" + "".join(f"\n       {p}" for p in problems))

    print("Addresses (features from mempool.space only, blockstream.info only, and alternating pages):")
    for address in addresses:
        results.append(await check_address(mempool, esplora, address))

    print(f"\n{sum(results)} of {len(results)} checks identical. "
          f"Requests: {mempool.name} {mempool.requests}, {esplora.name} {esplora.requests}.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:] or ADDRESSES)))
