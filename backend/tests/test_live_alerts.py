"""Offline tests for the live alert watcher (Alchemy alchemy_minedTransactions over a fake WebSocket)."""
import asyncio
import json

from fastapi.testclient import TestClient

from app.alerts import watcher as watcher_module
from app.alerts.watcher import MAX_WATCHED, AlertWatcher, limit_watchlist, subscribe_message
from app.main import app

CASE = "0x" + "a" * 40
SANCTIONED = "0x8589427373d6d84e98730d7795d8f6f8731fda16"
OTHER = "0x" + "b" * 40
URL = "wss://eth-mainnet.g.alchemy.com/v2/SECRETKEY123"


def event(tx_hash, frm, to, value=10**18, removed=False):
    return json.dumps({"jsonrpc": "2.0", "method": "eth_subscription", "params": {"subscription": "0xabc", "result": {
        "removed": removed, "transaction": {"hash": tx_hash, "from": frm, "to": to, "value": hex(value),
                                            "blockNumber": "0x10"}}}})


class FakeSocket:
    def __init__(self, messages):
        self.sent, self.messages = [], list(messages)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def recv(self):
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(3600)


async def watchlist():
    return {CASE: ["case #1: Ransom wallet"], SANCTIONED: ["OFAC sanctioned"]}


def test_subscription_is_always_filtered_by_address():
    msg = subscribe_message({CASE: ["x"]})
    assert msg["params"][0] == "alchemy_minedTransactions"
    assert msg["params"][1]["addresses"] == [{"from": CASE}, {"to": CASE}]


def test_watchlist_keeps_case_addresses_first_and_caps_size():
    big = {f"0x{i:040x}": ["OFAC sanctioned"] for i in range(MAX_WATCHED + 10)}
    big[CASE] = ["case #2: test"]
    big["not-an-address"] = ["case #3"]
    limited = limit_watchlist(big)
    assert len(limited) == MAX_WATCHED and next(iter(limited)) == CASE and "not-an-address" not in limited


def test_alerts_for_watched_addresses_only_and_no_duplicates():
    socket = FakeSocket([json.dumps({"id": 1, "result": "0xabc", "jsonrpc": "2.0"}),
                         event("0x01", CASE, OTHER), event("0x01", CASE, OTHER),  # duplicate
                         event("0x02", OTHER, SANCTIONED, 5 * 10**17),
                         event("0x03", OTHER, OTHER)])  # not watched
    w = AlertWatcher(URL, watchlist, connect=lambda *a, **k: socket, refresh_seconds=0.05)

    async def run_briefly():
        task = asyncio.create_task(w.run())
        await asyncio.sleep(0.2)
        task.cancel()

    asyncio.run(run_briefly())
    assert socket.sent[0]["params"][1]["addresses"][0] == {"from": CASE}
    newest, oldest = w.recent()
    assert (oldest["tx_hash"], oldest["direction"], oldest["sanctioned"]) == ("0x01", "out", False)
    assert (newest["tx_hash"], newest["direction"], newest["sanctioned"], newest["value_eth"]) == ("0x02", "in", True, "0.5")
    assert w.status.connected and w.status.watching == 2 and w.status.events == 2
    assert w.status.as_dict()["estimated_cu"] > 0
    assert w.recent(after_id=newest["id"]) == []


def test_refused_subscription_is_retried_and_key_never_shown():
    socket = FakeSocket([json.dumps({"id": 1, "error": {"message": f"bad key at {URL}"}})])
    w = AlertWatcher(URL, watchlist, connect=lambda *a, **k: socket)

    async def run_briefly():
        task = asyncio.create_task(w.run())
        await asyncio.sleep(0.1)
        task.cancel()

    asyncio.run(run_briefly())
    assert not w.status.connected and w.status.reconnects == 1
    assert "SECRETKEY123" not in (w.status.last_error or "x") and w.status.last_error


def test_alerts_route_says_when_off_and_lists_alerts():
    client = TestClient(app)
    watcher_module.set_watcher(None)
    assert client.get("/api/alerts").json()["status"]["enabled"] is False
    w = AlertWatcher(URL, watchlist, connect=lambda *a, **k: None)
    w.watchlist = {CASE: ["case #1: Ransom wallet"]}
    w.handle(event("0x09", OTHER, CASE))
    watcher_module.set_watcher(w)
    try:
        body = client.get("/api/alerts").json()
    finally:
        watcher_module.set_watcher(None)
    assert body["alerts"][0]["tx_hash"] == "0x09" and body["alerts"][0]["direction"] == "in"
    assert "SECRETKEY123" not in json.dumps(body)


def test_busy_address_is_paused_and_dropped_from_the_subscription():
    from app.alerts.watcher import BUSY_LIMIT_PER_HOUR
    first = [json.dumps({"id": 1, "result": "0xabc"})] + \
        [event(f"0x{i:064x}", OTHER, CASE) for i in range(BUSY_LIMIT_PER_HOUR + 1)]
    sockets = [FakeSocket(first), FakeSocket([json.dumps({"id": 1, "result": "0xdef"})])]
    w = AlertWatcher(URL, watchlist, connect=lambda *a, **k: sockets.pop(0) if sockets else FakeSocket([]))

    async def run_briefly():
        task = asyncio.create_task(w.run())
        await asyncio.sleep(0.2)
        task.cancel()

    second = sockets[1]
    asyncio.run(run_briefly())
    assert CASE in w.status.paused and w.status.as_dict()["paused"][0]["address"] == CASE
    entries = second.sent[0]["params"][1]["addresses"]
    assert {"to": CASE} not in entries and {"to": SANCTIONED} in entries
