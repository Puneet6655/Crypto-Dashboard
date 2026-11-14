"""Offline tests for the hand-made live-alert watchlist: per-chain checks, the Solana/Bitcoin pollers and the routes."""
import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.alerts import manual
from app.alerts import watcher as watcher_module
from app.alerts.poller import BASE, BITCOIN, SOLANA, PollingWatcher, base_rows, bitcoin_rows
from app.alerts.watcher import AlertWatcher, limit_watchlist
from app.main import app

ETH = "0x" + "AB" * 20
SOL = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
BTC = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"


def test_addresses_are_checked_for_the_chosen_chain():
    assert manual.clean("ethereum", f"  {ETH} ") == (ETH.lower(), None)
    assert manual.clean("solana", SOL) == (SOL, None)
    assert manual.clean("bitcoin", BTC.upper()) == (BTC, None)
    assert manual.clean("bitcoin", "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2")[1] is None
    # The right format for another chain is refused.
    assert manual.clean("ethereum", SOL)[1] and manual.clean("solana", ETH)[1] and manual.clean("bitcoin", ETH)[1]
    assert manual.clean("solana", SOL.lower())[1]  # base58 is case-sensitive: this no longer decodes to 32 bytes
    assert manual.clean("ethereum", "")[1] == "Enter an address."
    assert manual.clean("dogecoin", ETH)[1]
    # Base uses Ethereum-format addresses.
    assert manual.clean("base", f" {ETH} ") == (ETH.lower(), None)
    assert manual.clean("base", SOL)[1] == "A Base address is 0x followed by 40 hexadecimal characters."


def test_watchlist_addresses_are_kept_before_ofac_ones():
    big = {f"0x{i:040x}": ["OFAC sanctioned"] for i in range(600)}
    big[ETH.lower()] = [manual.reason("Hot wallet")]
    limited = limit_watchlist(big)
    assert next(iter(limited)) == ETH.lower() and limited[ETH.lower()] == ["watchlist: Hot wallet"]


def _poller(config, rows_by_round):
    rounds = iter(rows_by_round)
    current = {}

    async def fetch(address):
        return current[address]

    async def load():
        return {"addr": ["watchlist: test"]}

    async def no_sleep(_):
        return None

    w = PollingWatcher(config, fetch, load, sleep=no_sleep)

    async def go():
        for rows in rounds:
            current["addr"] = rows
            await w.run_round()

    return w, go


def test_poller_records_first_then_alerts_on_new_transactions_once():
    old = {"tx": "s1", "block": 1}
    new = {"tx": "s2", "block": 2, "failed": True}
    w, go = _poller(SOLANA, [[old], [new, old], [new, old]])
    asyncio.run(go())
    assert [a["tx_hash"] for a in w.recent()] == ["s2"]
    alert = w.recent()[0]
    assert alert["chain"] == "solana" and alert["failed"] and alert["watched"][0]["reasons"] == ["watchlist: test"]
    status = w.status()
    assert status["calls"] == 3 and status["estimated_cu"] == 3 * 40 and status["watching"] == 1


def test_solana_rounds_are_spaced_to_cap_compute_units():
    w, _ = _poller(SOLANA, [])
    w.watchlist = {f"a{i}": [] for i in range(20)}
    # 20 addresses, at least 30 s between calls: a round takes 10 minutes; one call per 30 s = 40 CU per 30 s.
    assert w.round_seconds() == 600
    w.watchlist = {"a": []}
    assert w.round_seconds() == 300
    assert 30 * 24 * 3600 / SOLANA.min_call_spacing * SOLANA.cu_per_call < 4_000_000


def test_bitcoin_rows_give_direction_amount_and_counterparty():
    me, other = BTC, "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"
    e = lambda a, v: SimpleNamespace(address=a, value_sats=v)  # noqa: E731
    incoming = SimpleNamespace(txid="t1", block_height=5, inputs=[e(other, 60_000)], outputs=[e(me, 50_000), e(other, 9_000)])
    outgoing = SimpleNamespace(txid="t2", block_height=None, inputs=[e(me, 50_000)], outputs=[e(other, 49_000)])
    rows = bitcoin_rows(me, [incoming, outgoing])
    assert rows[0] == {"tx": "t1", "block": 5, "failed": False, "direction": "in", "amount": "0.0005", "counterparty": other}
    assert rows[1]["direction"] == "out" and rows[1]["amount"] == "0.0005" and rows[1]["counterparty"] == other
    assert BITCOIN.cu_per_call == 0


def test_alerts_route_merges_chains_newest_first():
    client = TestClient(app)
    eth = AlertWatcher("wss://x/v2/KEY", None, connect=lambda *a, **k: None)
    sol, go = _poller(SOLANA, [[{"tx": "s1"}], [{"tx": "s2"}, {"tx": "s1"}]])
    asyncio.run(go())
    eth.watchlist = {ETH.lower(): ["watchlist"]}
    eth.handle('{"method":"eth_subscription","params":{"result":{"transaction":{"hash":"0x01","from":"%s","to":null,'
               '"value":"0x0"}}}}' % ETH.lower())
    watcher_module.set_watcher(eth)
    watcher_module.set_chain_watcher("solana", sol)
    try:
        body = client.get("/api/alerts").json()
    finally:
        watcher_module.set_watcher(None)
        watcher_module.set_chain_watcher("solana", None)
    assert [a["chain"] for a in body["alerts"]] == ["ethereum", "solana"]
    assert body["alerts"][0]["unit"] == "ETH" and body["alerts"][0]["amount"] == "0"
    assert body["statuses"]["solana"]["mode"] == "polling" and body["statuses"]["bitcoin"]["enabled"] is False
    assert body["status"]["chain"] == "ethereum"


def test_nudge_reloads_the_ethereum_list_without_waiting():
    loads = []

    async def load():
        loads.append(1)
        return {}

    w = AlertWatcher("wss://x/v2/KEY", load, connect=lambda *a, **k: None, refresh_seconds=3600)

    async def go():
        task = asyncio.create_task(w.run())
        await asyncio.sleep(0.05)
        w.nudge()
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(go())
    assert len(loads) == 2


def test_watchlist_routes_need_the_database():
    client = TestClient(app)
    assert client.get("/api/alerts/watchlist").status_code in (401, 503)


def test_base_rows_pick_eth_over_tokens_and_give_direction_and_unit():
    me, other = ETH.lower(), "0x" + "cd" * 20
    transfers = [  # newest first, as recent_transfers gives them
        {"hash": "0xT2", "blockNum": "0x10", "category": "erc20", "from": other, "to": me, "value": 12.5, "asset": "USDC"},
        {"hash": "0xT1", "blockNum": "0xf", "category": "erc20", "from": me, "to": other, "value": 3, "asset": "DEGEN"},
        {"hash": "0xT1", "blockNum": "0xf", "category": "external", "from": me.upper().replace("0X", "0x"), "to": other,
         "value": 0.0123, "asset": "ETH"},
    ]
    rows = base_rows(me, transfers)
    assert [r["tx"] for r in rows] == ["0xt2", "0xt1"]
    assert rows[0] == {"tx": "0xt2", "block": 16, "failed": False, "direction": "in", "amount": "12.5", "unit": "USDC",
                       "counterparty": other}
    assert rows[1]["direction"] == "out" and rows[1]["unit"] == "ETH" and rows[1]["amount"] == "0.0123"


def test_base_poller_alerts_with_the_token_unit_and_caps_compute_units():
    w, go = _poller(BASE, [[{"tx": "0xa"}], [{"tx": "0xb", "unit": "USDC", "amount": "5", "direction": "in"}, {"tx": "0xa"}]])
    asyncio.run(go())
    alert = w.recent()[0]
    assert alert["chain"] == "base" and alert["unit"] == "USDC" and alert["amount"] == "5"
    assert w.status()["estimated_cu"] == 2 * 240
    # One check (240 CU) every 3 minutes at most: under 3.5 million CU a month whatever the list size.
    assert 30 * 24 * 3600 / BASE.min_call_spacing * BASE.cu_per_call < 3_500_000
