"""SYNTHETIC teaching fixture.

These dictionaries copy the *shape* of mempool.space responses so tests run
offline. The numbers are invented. They are not real balances or research data.
"""
SYNTHETIC_ADDRESS = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
SYNTHETIC_TXID = "a" * 64

ADDRESS_RESPONSE = {
    "address": SYNTHETIC_ADDRESS,
    "chain_stats": {"funded_txo_count": 3, "funded_txo_sum": 150000, "spent_txo_count": 1, "spent_txo_sum": 50000, "tx_count": 4},
    "mempool_stats": {"funded_txo_count": 1, "funded_txo_sum": 2000, "spent_txo_count": 0, "spent_txo_sum": 0, "tx_count": 1},
}

TX_RESPONSE = {
    "txid": SYNTHETIC_TXID,
    "fee": 1200,
    "vin": [{"prevout": {"scriptpubkey_address": SYNTHETIC_ADDRESS, "value": 100000}}],
    "vout": [
        {"scriptpubkey_address": "1BoatSLRHtKNngkdXEeobR76b53LETtpyT", "value": 60000},
        {"scriptpubkey_address": SYNTHETIC_ADDRESS, "value": 38800},
    ],
    "status": {"confirmed": True, "block_height": 800000, "block_time": 1690000000},
}
