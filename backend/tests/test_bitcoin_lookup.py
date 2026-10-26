"""Offline tests: a fake transport replays the synthetic fixture instead of calling the internet."""
import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.providers.bitcoin_mempool import MempoolSpaceProvider
from app.providers.registry import get_bitcoin_provider
from tests.fixtures.mempool_synthetic import ADDRESS_RESPONSE, SYNTHETIC_ADDRESS, SYNTHETIC_TXID, TX_RESPONSE


def fake_mempool(request: httpx.Request) -> httpx.Response:
    """Plays the part of mempool.space. Returns a canned response for each known path."""
    path = request.url.path
    if path == f"/api/address/{SYNTHETIC_ADDRESS}":
        return httpx.Response(200, json=ADDRESS_RESPONSE)
    if path == f"/api/tx/{SYNTHETIC_TXID}":
        return httpx.Response(200, json=TX_RESPONSE)
    if path == "/api/blocks/tip/height":
        return httpx.Response(200, text="800123")
    if path == "/api/tx/" + "b" * 64:
        return httpx.Response(429, text="Too Many Requests")
    return httpx.Response(400, text="Invalid Bitcoin address")


def use_fake_provider():
    app.dependency_overrides[get_bitcoin_provider] = lambda: MempoolSpaceProvider(
        base_url="https://mempool.test/api", network="mainnet", timeout_seconds=5,
        transport=httpx.MockTransport(fake_mempool),
    )


client = TestClient(app)


def test_health():
    assert client.get("/api/health").json()["status"] == "ok"


def test_address_lookup_separates_confirmed_and_unconfirmed():
    use_fake_provider()
    body = client.get(f"/api/bitcoin/address/{SYNTHETIC_ADDRESS}").json()
    assert body["entity_id"] == f"bitcoin:mainnet:address:{SYNTHETIC_ADDRESS}"
    assert body["confirmed"]["balance_sats"] == 100000
    assert body["unconfirmed"]["tx_count"] == 1
    assert body["provenance"]["provider"] == "mempool.space"


def test_transaction_lookup_keeps_all_inputs_and_outputs():
    use_fake_provider()
    body = client.get(f"/api/bitcoin/tx/{SYNTHETIC_TXID}").json()
    assert body["status"] == "confirmed"
    assert len(body["inputs"]) == 1 and len(body["outputs"]) == 2
    assert body["fee_sats"] == 1200


def test_invalid_address_is_rejected_before_calling_provider():
    response = client.get("/api/bitcoin/address/not-an-address")
    assert response.status_code == 422


def test_rate_limit_is_reported_as_429():
    use_fake_provider()
    response = client.get("/api/bitcoin/tx/" + "b" * 64)
    assert response.status_code == 429
    assert "rate limit" in response.json()["detail"]


def test_provider_health_reports_tip_height():
    use_fake_provider()
    assert client.get("/api/bitcoin/health").json()["tip_height"] == 800123
