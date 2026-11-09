"""Offline tests for the shared wallet-window features. All transactions here are invented."""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.features.wallet_window import (
    FEATURE_NAMES,
    BlockStat,
    TxRecord,
    address_type_features,
    compute_wallet_features,
    exposure_features,
    fetch_window_transactions,
    network_context,
    tx_record_from_summary,
    window_counterparties,
)
from app.providers.bitcoin_mempool import MempoolSpaceProvider
from tests.fixtures.mempool_synthetic import SYNTHETIC_ADDRESS, TX_RESPONSE

A, B, C, D = "1AddrA", "1AddrB", "3AddrC", "bc1qaddrd"
T0 = datetime(2017, 1, 1, tzinfo=timezone.utc)


def tx(txid, height, inputs, outputs, fee=1_000, day=0):
    value_in = sum(v for _, v in inputs)
    return TxRecord(txid, height, T0 + timedelta(days=day), fee, value_in, tuple(inputs), tuple(outputs))


# A receives 1 BTC from B, then pays 0.3 BTC to C with change back to itself, then receives from B and D.
HISTORY = [
    tx("t1", 100, [(B, 100_001_000)], [(A, 100_000_000)], day=0),
    tx("t2", 150, [(A, 100_000_000)], [(C, 30_000_000), (A, 69_999_000)], day=1),
    tx("t3", 160, [(B, 50_000_000), (D, 50_001_000)], [(A, 100_000_000)], day=1),
]


def test_counts_amounts_and_counterparties():
    f = compute_wallet_features(A, HISTORY)
    assert f["anchor_block"] == 160
    assert (f["total_txs"], f["num_txs_as_sender"], f["num_txs_as_receiver"]) == (3, 1, 3)
    assert f["lifetime_in_blocks"] == 60
    assert f["blocks_since_first_sent"] == 10 and f["blocks_since_first_received"] == 60
    assert f["num_active_days"] == 2
    assert f["btc_sent_total"] == 1.0
    assert f["btc_received_total"] == pytest.approx(2.69999)
    assert f["btc_transacted_max"] == 1.69999  # t2: 1.0 sent + 0.69999 change received
    assert f["fees_total"] == pytest.approx(0.00003)
    assert f["blocks_btwn_txs_total"] == 60 and f["blocks_btwn_txs_median"] == 30
    assert f["blocks_btwn_input_txs_total"] == 0  # only one sending transaction
    # t1: B; t2: C (sent) and nobody new (change is A itself); t3: B and D. B appears twice.
    assert f["num_counterparties"] == 3
    assert f["num_addr_transacted_multiple"] == 1
    assert f["transacted_w_address_total"] == 4 and f["transacted_w_address_max"] == 2
    assert f["window_truncated"] == 0
    assert set(FEATURE_NAMES) <= set(f)


def test_window_cap_and_observation_point():
    capped = compute_wallet_features(A, HISTORY, max_txs=2)
    assert capped["total_txs"] == 2 and capped["window_truncated"] == 1
    assert capped["lifetime_in_blocks"] == 10  # only the two newest: blocks 150 and 160
    short = compute_wallet_features(A, HISTORY, window_blocks=20)
    assert short["total_txs"] == 2  # block 100 is outside 141..160
    earlier = compute_wallet_features(A, HISTORY, observed_at_block=155)
    assert earlier["anchor_block"] == 150 and earlier["total_txs"] == 2


def test_empty_history_gives_zeros():
    f = compute_wallet_features(C, [])
    assert f["anchor_block"] is None
    assert all(f[name] == 0 for name in FEATURE_NAMES)


def test_live_summary_conversion_matches_record():
    provider = MempoolSpaceProvider("https://mempool.test/api", "mainnet", 5)
    summary = provider._to_tx_summary(TX_RESPONSE, "https://mempool.test/api/tx/x")
    record = tx_record_from_summary(summary)
    assert record.fee_sats == 1200 and record.input_value_sats == 100000
    assert record.inputs == ((SYNTHETIC_ADDRESS, 100000),)
    assert len(record.outputs) == 2 and record.block_height == 800000


def test_coinbase_has_no_fee_or_input_value():
    provider = MempoolSpaceProvider("https://mempool.test/api", "mainnet", 5)
    coinbase = dict(TX_RESPONSE, fee=0, vin=[{"prevout": None, "is_coinbase": True}])
    record = tx_record_from_summary(provider._to_tx_summary(coinbase, "u"))
    assert record.fee_sats == 0 and record.input_value_sats == 0 and record.inputs == ()


def test_network_context():
    blocks = [BlockStat(h, 11, 1_000_000) for h in range(1, 201)]
    ctx = network_context(blocks, anchor_block=200, fees_mean_btc=0.002)
    assert ctx["net_tx_count_144"] == 144 * 11
    assert ctx["net_avg_fee_btc_144"] == 0.001  # 1,000,000 sats over 10 non-coinbase transactions
    assert ctx["fee_vs_network_mean"] == 2.0


def test_live_paging_stops_at_window_edge():
    """60 transactions, one per 100 blocks: a 4032-block window holds the newest 41."""
    def make(i):
        return {"txid": f"{i:064x}", "fee": 100, "vin": [{"prevout": {"scriptpubkey_address": A, "value": 1000}}],
                "vout": [{"scriptpubkey_address": B, "value": 900}],
                "status": {"confirmed": True, "block_height": 10_000 - 100 * i, "block_time": 1_500_000_000}}
    history = [make(i) for i in range(60)]
    calls = []

    def fake(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/txs"):
            return httpx.Response(200, json=history[:25])
        last = request.url.path.rsplit("/", 1)[1]
        start = next(i for i, t in enumerate(history) if t["txid"] == last) + 1
        return httpx.Response(200, json=history[start:start + 25])

    provider = MempoolSpaceProvider("https://mempool.test/api", "mainnet", 5, transport=httpx.MockTransport(fake))
    records = asyncio.run(fetch_window_transactions(provider, A))
    assert len(records) == 41 and len(calls) == 2
    assert compute_wallet_features(A, records)["total_txs"] == 41


def test_round_amounts_change_and_short_window():
    f = compute_wallet_features(A, HISTORY)
    # Receiving txs: t1 (1 BTC, round), t2 (0.69999 change, not round), t3 (1 BTC, round).
    assert f["round_received_share"] == pytest.approx(2 / 3)
    assert f["round_payment_share"] == 1.0  # t2 paid C 0.3 BTC
    assert f["self_change_share"] == 1.0  # t2 sent change back to A
    assert f["total_txs_1w"] == 3 and f["num_counterparties_1w"] == 3
    late = [tx("t4", 2000, [(B, 5_000)], [(A, 4_000)])]
    g = compute_wallet_features(A, HISTORY + late)
    assert g["total_txs_1w"] == 1 and g["btc_received_total_1w"] == 0.00004  # only t4 is within 1008 blocks


def test_fee_rate_and_transaction_shape():
    record = TxRecord("x", 10, T0, 1_000, 5_000, ((A, 5_000),), ((B, 4_000),), vsize=250, input_count=1, output_count=3)
    f = compute_wallet_features(A, [record])
    assert f["fee_rate_mean"] == 4.0
    assert f["tx_outputs_max"] == 3 and f["tx_inputs_mean"] == 1


def test_live_conversion_keeps_weight_and_counts():
    provider = MempoolSpaceProvider("https://mempool.test/api", "mainnet", 5)
    data = dict(TX_RESPONSE, weight=561, vout=TX_RESPONSE["vout"] + [{"scriptpubkey_type": "op_return", "value": 0}])
    record = tx_record_from_summary(provider._to_tx_summary(data, "u"))
    assert record.vsize == 141 and record.input_count == 1 and record.output_count == 3
    assert len(record.outputs) == 2  # the OP_RETURN output has no address


def test_exposure_to_known_labels():
    payers, payees = window_counterparties(A, HISTORY)
    assert payers == {B, D} and payees == {C}
    f = exposure_features(payers, payees, illicit={D}, licit={C})
    assert f["illicit_payers"] == 1 and f["illicit_payer_share"] == 0.5
    assert f["licit_payees"] == 1 and f["licit_payee_share"] == 1.0
    assert f["illicit_payees"] == 0


def test_address_type():
    assert address_type_features("bc1qxyz") == {"addr_p2pkh": 0.0, "addr_p2sh": 0.0, "addr_bech32": 1.0}
    assert address_type_features("3Abc")["addr_p2sh"] == 1.0
