"""Reading more than one page of an address's history, for providers whose page size is fixed.

mempool.space and Blockstream (Esplora) always answer an address history call with one page: the
unconfirmed transactions plus the newest 25 confirmed ones, and then 25 confirmed per page after a
given txid. The page size cannot be raised, so getting more history means following pages.
BTC_ADDRESS_PAGES (default 4, so about 100 transactions) says how many pages one lookup may read.
Paging stops early as soon as a page is not full, so a small wallet still costs one call.
"""
from __future__ import annotations

from typing import Awaitable, Callable

BTC_PAGE_SIZE = 25  # confirmed transactions per Esplora page (fixed by the server)


async def bitcoin_history(provider, address: str, max_pages: int,
                          pace: Callable[[], Awaitable[None]] | None = None) -> tuple[list, bool]:
    """(transactions newest first, complete). complete is True when the oldest page was reached.

    `pace` is awaited before every page after the first, so a caller can space calls out."""
    txs, seen, after = [], set(), None
    for page_no in range(max(1, max_pages)):
        if page_no and pace is not None:
            await pace()
        if after is None:
            page = await provider.get_address_transactions(address)
        else:
            page = await provider.get_address_transactions(address, after_txid=after)
        for tx in page:
            if tx.txid not in seen:
                seen.add(tx.txid)
                txs.append(tx)
        confirmed = [tx for tx in page if tx.status == "confirmed"]
        if len(confirmed) < BTC_PAGE_SIZE:
            return txs, True
        after = confirmed[-1].txid
    return txs, False
