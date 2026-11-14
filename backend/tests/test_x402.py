"""x402: snapshot, rules, Base reader (Blockscout and Alchemy answers recorded as fixtures), investigation mode,
monitor and API routes. Offline: every HTTP answer is a stand-in."""
import asyncio
import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.x402 import snapshot as snapmod
from app.x402.chain import AUTHORIZATION_USED_TOPIC, TRANSFER_TOPIC, BaseX402Reader
from app.x402.rules import Payment, alert_worthy, check_address, check_payment, worst
from app.x402.snapshot import USDC_BASE, Snapshot

FAC = "0x" + "f" * 40
SERVER = "0x" + "5" * 40          # tainted gift-card server
CLEAN_SERVER = "0x" + "6" * 40
PHISHER = "0x" + "b" * 40
FUNDED = "0x" + "c" * 40          # client funded by the phisher
CLIENT = "0x" + "a" * 40
TX = "0x" + "1" * 64


def _write(folder, name, value):
    (folder / name).write_text(json.dumps(value))


@pytest.fixture
def snap(tmp_path, monkeypatch):
    _write(tmp_path, "meta.json", {"built_at": "2026-11-14T00:00:00+00:00", "counts": {"payment_addresses": 2},
                                   "gathered": {"services": "2026-10-08"}, "sources": []})
    _write(tmp_path, "facilitators.json", [{"address": FAC, "chain": "base", "names": ["Coinbase"],
                                            "sources": ["x402scan"], "deprecated": False}])
    _write(tmp_path, "servers.json", {
        SERVER: {"chain": "base", "servers": [{"name": "Gift shop", "url": "https://gift.example"}],
                 "categories": {"*giftcard-purchase": 3}, "services": 3, "min_price": 0.01, "max_price": 0.05,
                 "docs": False, "countries": ["Canada"], "risk": "high", "shared_by": 1},
        CLEAN_SERVER: {"chain": "base", "servers": [{"name": "Weather", "url": "https://w.example"}],
                       "categories": {"weather": 2}, "services": 2, "min_price": 0.001, "max_price": 0.01,
                       "docs": True, "countries": [], "risk": None, "shared_by": 1}})
    _write(tmp_path, "phishing.json", [{"address": PHISHER, "kinds": ["funder"], "name": "Fake_Phishing1",
                                        "tags": "Phish / Hack", "notes": None, "from": None}])
    _write(tmp_path, "phishing_funded.json", {FUNDED: {"funder": PHISHER, "funder_name": "Fake_Phishing1",
                                                       "kinds": ["client"], "names": [], "usd": 25.0,
                                                       "first": "2026-09-01T00:00:00Z"}})
    _write(tmp_path, "taint.json", {SERVER: {"rank": 1, "usdc_received": 1000.0, "transfers": 10, "clients": 4,
                                             "median_transfer": 1.0, "from_phishing": 200.0,
                                             "from_phishing_percent": 20.0, "phishing_funded": False,
                                             "chain": "base"}})
    _write(tmp_path, "clients.json", {FUNDED: [12, 30.5, 2, "Gift shop"]})
    monkeypatch.setenv("X402_DATA_DIR", str(tmp_path))
    snapmod.get_snapshot.cache_clear()
    yield snapmod.get_snapshot()
    snapmod.get_snapshot.cache_clear()


# ---- snapshot and rules ---------------------------------------------------------------------

def test_snapshot_lookups(snap: Snapshot):
    assert snap.loaded
    assert snap.facilitator(FAC.upper().replace("0X", "0x"))["names"] == ["Coinbase"]
    assert snap.role_of(FUNDED) == ["client"]
    assert snap.client(FUNDED)["servers_paid"] == 2
    assert snap.risky_servers()[0]["address"] == SERVER
    assert snap.facilitator_addresses("base") == [FAC]


def test_payment_from_phishing_funded_client_to_tainted_giftcard_server(snap):
    p = Payment("base", TX, FUNDED, SERVER, 80.0, facilitator=FAC, gasless=True)
    hits = check_payment(snap, p)
    rules = {h.rule for h in hits}
    assert {"phishing_funded", "tainted_server", "risky_service", "cash_out_purchase"} <= rules
    assert "odd_amount" not in rules  # 80 is under the US$100 floor
    assert worst(hits) == "high"
    assert alert_worthy(hits)


def test_ordinary_payment_raises_nothing(snap):
    hits = check_payment(snap, Payment("base", TX, CLIENT, CLEAN_SERVER, 0.01, facilitator=FAC, gasless=True))
    assert hits == []
    assert not alert_worthy(hits)


def test_server_context_alone_does_not_alert(snap):
    hits = check_payment(snap, Payment("base", TX, CLIENT, SERVER, 0.02, facilitator=FAC, gasless=True))
    assert {"tainted_server", "risky_service"} <= {h.rule for h in hits}
    assert not alert_worthy(hits)


def test_time_bounded_label(snap):
    snap.phishing[PHISHER]["from"] = "2026-10-01T00:00:00Z"
    early = check_payment(snap, Payment("base", TX, PHISHER, CLEAN_SERVER, 1.0, at="2026-09-01T00:00:00+00:00"))
    assert [h.rule for h in early] == ["phishing_label_later"]
    late = check_payment(snap, Payment("base", TX, PHISHER, CLEAN_SERVER, 1.0, at="2026-10-02T00:00:00+00:00"))
    assert "phishing_address" in {h.rule for h in late}


def test_unknown_facilitator_and_large_payment(snap):
    other = "0x" + "9" * 40
    hits = check_payment(snap, Payment("base", TX, CLIENT, CLEAN_SERVER, 5000.0, facilitator=other, gasless=True))
    assert {"unknown_facilitator", "large_payment"} <= {h.rule for h in hits}


def test_sanctions_rule_uses_the_indicator_index(snap):
    class Hit:
        title, label, family = "OFAC SDN", "sanctioned", None

    class Index:
        def lookup(self, address, sources):
            return [Hit()] if address == CLIENT else []

    hits = check_address(snap, CLIENT, "base", Index())
    assert hits[0].rule == "sanctions_or_scam_list" and hits[0].severity == "high"


# ---- Base reader ----------------------------------------------------------------------------

def blockscout_tx(sender=FAC, method="transferWithAuthorization", value="80000000", payer=FUNDED, to=SERVER):
    return {"hash": TX, "from": {"hash": sender}, "method": method, "timestamp": "2026-11-14T10:00:00Z",
            "block_number": 123, "status": "ok", "token_transfers": [
                {"token": {"address_hash": USDC_BASE}, "from": {"hash": payer}, "to": {"hash": to},
                 "total": {"value": value, "decimals": "6"}, "method": method, "transaction_hash": TX}]}


def reader_with(handler, snap, **kw):
    return BaseX402Reader(snap, transport=httpx.MockTransport(handler), min_interval=0, **kw)


def test_reader_decodes_x402_tx_from_blockscout(snap):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=blockscout_tx())

    data = asyncio.run(reader_with(handler, snap).transaction(TX))
    assert seen[0].startswith("https://base.blockscout.com/api/v2/transactions/")
    p = data["payments"][0]
    assert (p.payer, p.pay_to, p.amount, p.facilitator, p.gasless) == (FUNDED, SERVER, 80.0, FAC, True)


def test_reader_uses_pro_api_with_key(snap):
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, json=blockscout_tx())

    asyncio.run(reader_with(handler, snap, blockscout_key="k").transaction(TX))
    assert str(seen[0]).startswith("https://api.blockscout.com/8453/api/v2/transactions/")
    assert seen[0].params["apikey"] == "k"


def test_reader_falls_back_to_alchemy_receipt(snap):
    pad = lambda a: "0x" + "0" * 24 + a[2:]  # noqa: E731

    def handler(request):
        if "blockscout" in request.url.host:
            return httpx.Response(503)
        body = json.loads(request.content)
        if body["method"] == "eth_getTransactionByHash":
            return httpx.Response(200, json={"result": {"from": FAC, "blockNumber": "0x7b"}})
        return httpx.Response(200, json={"result": {"status": "0x1", "logs": [
            {"address": USDC_BASE, "topics": [AUTHORIZATION_USED_TOPIC, pad(FUNDED), "0x" + "2" * 64]},
            {"address": USDC_BASE, "topics": [TRANSFER_TOPIC, pad(FUNDED), pad(SERVER)], "data": hex(80_000_000)}]}})

    data = asyncio.run(reader_with(handler, snap, alchemy_key="a").transaction(TX))
    p = data["payments"][0]
    assert data["source"] == "Alchemy (Base)"
    assert (p.payer, p.pay_to, p.amount, p.facilitator, p.gasless, p.block) == (FUNDED, SERVER, 80.0, FAC, True, 123)


def test_is_x402(snap):
    r = BaseX402Reader(snap)
    assert r.is_x402(Payment("base", TX, CLIENT, CLEAN_SERVER, 1, facilitator=FAC))
    assert r.is_x402(Payment("base", TX, CLIENT, SERVER, 1, gasless=True))
    assert not r.is_x402(Payment("base", TX, CLIENT, "0x" + "7" * 40, 1, facilitator="0x" + "8" * 40))


# ---- investigation mode ---------------------------------------------------------------------

def test_x402_investigation_of_a_transaction(snap):
    from app.schemas.investigation import InvestigationRequest
    from app.x402.investigation import investigate_x402

    reader = reader_with(lambda request: httpx.Response(200, json=blockscout_tx()), snap)
    req = InvestigationRequest(mode="x402", input_type="base_txid", value=TX, method="x402_rules")
    result = asyncio.run(investigate_x402(req, 50, None, reader))
    titles = " | ".join(f.title for f in result.findings)
    assert "x402 payment settled by Coinbase" in titles
    assert "funded by a phishing-tagged address" in titles
    types = {n.type for n in result.graph.nodes}
    assert {"payment", "address"} <= types
    assert any(e.label == "settled" for e in result.graph.edges)
    assert any(e.label.startswith("funded") for e in result.graph.edges)
    assert any(i.rule == "phishing_funded" and i.matched for i in result.indicators)


def test_x402_investigation_of_a_solana_address_uses_the_snapshot(snap):
    from app.schemas.investigation import InvestigationRequest
    from app.x402.investigation import investigate_x402

    req = InvestigationRequest(mode="x402", input_type="solana_address",
                               value="5yASLjtNssGXDv6bR9e71WXKxRymDiUzbgkGPqkQNgRn", method="x402_rules")
    result = asyncio.run(investigate_x402(req, 50))
    assert "not in the x402 snapshot" in result.findings[0].title


# ---- monitor --------------------------------------------------------------------------------

def test_monitor_alerts_on_new_flagged_payment_only(snap):
    from app.x402.monitor import X402Monitor

    rounds = {"n": 0}

    def handler(request):
        if "/token-transfers" in request.url.path:
            items = [{"transaction_hash": "0x" + "3" * 64, "from": {"hash": CLIENT}, "to": {"hash": SERVER},
                      "total": {"value": "20000"}, "method": "transferWithAuthorization",
                      "timestamp": "2026-11-14T09:00:00Z", "block_number": 100}]
            if rounds["n"]:
                items.insert(0, {"transaction_hash": TX, "from": {"hash": FUNDED}, "to": {"hash": SERVER},
                                 "total": {"value": "80000000"}, "method": "transferWithAuthorization",
                                 "timestamp": "2026-11-14T10:00:00Z", "block_number": 101})
            return httpx.Response(200, json={"items": items})
        return httpx.Response(200, json={"from": {"hash": FAC}})

    async def no_sleep(_):
        return None

    m = X402Monitor(snap, reader_with(handler, snap), watch_count=5, sleep=no_sleep)
    assert list(m.watch) == [SERVER]
    asyncio.run(m.watch_round())
    assert m.alerts == type(m.alerts)(maxlen=m.alerts.maxlen) and len(m.feed) == 1  # first look: feed only
    rounds["n"] = 1
    asyncio.run(m.watch_round())
    assert len(m.alerts) == 1
    alert = m.alerts[0]
    assert alert["kind"] == "x402" and alert["tx_hash"] == TX and alert["unit"] == "USDC"
    assert alert["x402"]["facilitator_name"] == "Coinbase"
    assert m.status()["chain"] == "x402" and m.status()["payments_seen"] == 2


def test_monitor_network_round_with_cdp(snap):
    from app.x402.monitor import X402Monitor

    class FakeCdp:
        queries = 0

        async def x402_payments(self, facilitators, since):
            assert facilitators == [FAC]
            return [Payment("base", TX, FUNDED, SERVER, 80.0, facilitator=FAC, at="2026-11-14T10:00:00+00:00",
                            gasless=True)]

    m = X402Monitor(snap, BaseX402Reader(snap), cdp=FakeCdp(), watch_count=0)
    made = asyncio.run(m.network_round())
    assert made == 1 and m.status()["network"] is True
    assert asyncio.run(m.network_round()) == 0  # same payment again: not counted twice


# ---- CDP SQL client -------------------------------------------------------------------------

def test_cdp_sql_and_token(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
    import jwt
    from datetime import datetime, timezone

    from app.x402 import cdp

    sql = cdp.payments_sql([FAC, "0xBAD'; drop"], datetime(2026, 11, 14, tzinfo=timezone.utc))
    assert f"'{FAC}'" in sql and "drop" not in sql and "LIMIT 1000" in sql and "base.events" in sql
    raw = Ed25519PrivateKey.generate().private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    token = cdp.bearer_token("kid-1", base64.b64encode(raw).decode())
    claims = jwt.decode(token, options={"verify_signature": False}, audience=["cdp_service"])
    assert claims["uri"] == "POST api.cdp.coinbase.com/platform/v2/data/query/run" and claims["sub"] == "kid-1"
    monkeypatch.delenv("CDP_API_KEY_ID", raising=False)
    monkeypatch.delenv("CDP_API_KEY_NAME", raising=False)
    assert cdp.credentials() is None and cdp.CdpSqlClient.from_env() is None

    def handler(request):
        assert request.headers["Authorization"].startswith("Bearer ")
        return httpx.Response(200, json={"result": [{"transaction_hash": TX, "transaction_from": FAC,
                                                     "block_number": 5, "block_timestamp": "2026-11-14 10:00:00",
                                                     "payer": FUNDED, "pay_to": SERVER, "value": "80000000"}]})

    client = cdp.CdpSqlClient("kid-1", base64.b64encode(raw).decode(), transport=httpx.MockTransport(handler))
    rows = asyncio.run(client.x402_payments([FAC], datetime(2026, 11, 14, tzinfo=timezone.utc)))
    assert rows[0].amount == 80.0 and rows[0].at == "2026-11-14T10:00:00+00:00" and rows[0].facilitator == FAC


# ---- API ------------------------------------------------------------------------------------

def test_routes(snap):
    from app.main import app

    client = TestClient(app)
    body = client.get("/api/x402/summary").json()
    assert body["snapshot"]["loaded"] is True and body["sources"]["cdp"]["configured"] in (True, False)
    assert client.get("/api/x402/risky-servers").json()["servers"][0]["address"] == SERVER
    a = client.get(f"/api/x402/address/{SERVER}").json()
    assert a["roles"] == ["server"] and a["severity"] == "high"
    assert client.get("/api/x402/tx/0x12").status_code == 422
    assert client.get("/api/x402/payments").json()["payments"] == []
