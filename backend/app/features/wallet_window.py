"""Wallet features over a fixed look-back window. One definition for training and live data.

Training rows (built from BigQuery's public Bitcoin data) and live lookups (mempool.space)
both turn transactions into TxRecord objects and call compute_wallet_features(). The SQL in
training/sql/wallet_window_features.sql implements the same rules for bulk data, and
training/check_live_parity.py checks that both give the same numbers for real addresses.

The rules, in plain words:

* Anchor: the wallet's newest confirmed transaction at or before the observation block.
  Training observes a wallet at an Elliptic++ time step; live observes it now.
* Window: the WINDOW_BLOCKS blocks ending at the anchor (about four weeks).
* Cap: only the MAX_TXS newest transactions in the window are used (newest block first,
  ties broken by txid, descending). window_truncated = 1 when the window held more.
* Only inputs and outputs with one standard address count (P2PKH, P2SH, P2WPKH, P2WSH,
  P2TR). mempool.space shows no address for the others (P2PK, multisig, OP_RETURN).
* Amounts are BTC. A statistic over an empty list is 0, as in Elliptic++.

Feature names follow Elliptic++ where the idea is the same, but the values are NOT the
Elliptic++ CSV values: those count only transactions inside the Elliptic++ sample, these
count every on-chain transaction in the window.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime

WINDOW_BLOCKS = 4032  # two Elliptic++ time steps (2 x 2016 blocks), about four weeks
WEEK_BLOCKS = 1008  # the short window: the last week of the main window
MAX_TXS = 100  # newest transactions kept per window; four mempool.space pages
NETWORK_BLOCKS = 144  # network context: the 144 blocks (about one day) ending at the anchor
SATS_PER_BTC = 100_000_000
ROUND_SATS = 100_000  # an amount is "round" when it is a whole multiple of 0.001 BTC

STAT_SUFFIXES = ("total", "min", "max", "mean", "median")
STAT_FAMILIES = (
    "btc_transacted",
    "btc_sent",
    "btc_received",
    "fees",
    "fees_as_share",
    "blocks_btwn_txs",
    "blocks_btwn_input_txs",
    "blocks_btwn_output_txs",
    "transacted_w_address",
    "fee_rate",  # sat/vB of each transaction
    "tx_inputs",  # number of inputs of each transaction (all of them, with or without an address)
    "tx_outputs",
)
FEATURE_NAMES = (
    "total_txs",
    "num_txs_as_sender",
    "num_txs_as_receiver",
    "lifetime_in_blocks",
    "blocks_since_first_sent",
    "blocks_since_first_received",
    "num_active_days",
    "num_counterparties",
    "num_addr_transacted_multiple",
    "window_truncated",
    "round_received_share",  # receiving transactions where it got a round amount
    "round_payment_share",  # sending transactions that paid someone else a round amount
    "self_change_share",  # sending transactions that also paid change back to itself
    "total_txs_1w",
    "btc_received_total_1w",
    "btc_sent_total_1w",
    "num_counterparties_1w",
) + tuple(f"{family}_{suffix}" for family in STAT_FAMILIES for suffix in STAT_SUFFIXES)
NETWORK_FEATURE_NAMES = ("net_tx_count_144", "net_avg_fee_btc_144", "fee_vs_network_mean")
ADDRESS_FEATURE_NAMES = ("addr_p2pkh", "addr_p2sh", "addr_bech32")
# Counterparties in known-label lists: payers paid this wallet, payees were paid by it.
EXPOSURE_FEATURE_NAMES = (
    "illicit_payers", "illicit_payees", "licit_payers", "licit_payees",
    "illicit_payer_share", "illicit_payee_share", "licit_payer_share", "licit_payee_share",
)


@dataclass(frozen=True)
class TxRecord:
    """One confirmed transaction, reduced to what the features need.

    inputs and outputs hold (address, value in satoshis) for every input or output that has
    one standard address. input_value_sats is the value of ALL inputs (0 for coinbase).
    """

    txid: str
    block_height: int
    block_time: datetime
    fee_sats: int
    input_value_sats: int
    inputs: tuple[tuple[str, int], ...]
    outputs: tuple[tuple[str, int], ...]
    vsize: int = 0  # virtual size in vbytes, (weight + 3) // 4; 0 when unknown
    input_count: int = 0  # every input, with or without an address (coinbase: 1)
    output_count: int = 0  # every output, including OP_RETURN


@dataclass(frozen=True)
class BlockStat:
    """Network totals for one block. tx_count includes the coinbase; fees exclude it."""

    height: int
    tx_count: int
    total_fees_sats: int


def tx_record_from_summary(tx) -> TxRecord | None:
    """Convert a live TransactionSummary (app.schemas.bitcoin). Returns None if unconfirmed."""
    if tx.status != "confirmed" or tx.block_height is None or tx.block_time is None:
        return None
    is_coinbase = all(endpoint.value_sats is None for endpoint in tx.inputs)
    return TxRecord(
        txid=tx.txid,
        block_height=tx.block_height,
        block_time=tx.block_time,
        fee_sats=0 if is_coinbase else int(tx.fee_sats or 0),
        input_value_sats=sum(int(e.value_sats) for e in tx.inputs if e.value_sats is not None),
        inputs=tuple((e.address, int(e.value_sats)) for e in tx.inputs if e.address and e.value_sats is not None),
        outputs=tuple((e.address, int(e.value_sats)) for e in tx.outputs if e.address and e.value_sats is not None),
        vsize=(int(tx.weight) + 3) // 4 if getattr(tx, "weight", None) else 0,
        input_count=len(tx.inputs),
        output_count=len(tx.outputs),
    )


def address_type_features(address: str) -> dict[str, float]:
    """Script type read from the address text: 1... P2PKH, 3... P2SH, bc1... SegWit."""
    return {
        "addr_p2pkh": float(address.startswith("1")),
        "addr_p2sh": float(address.startswith("3")),
        "addr_bech32": float(address.startswith("bc1")),
    }


def _stats(values: list[float]) -> tuple[float, float, float, float, float]:
    """(total, min, max, mean, median); all zeros for an empty list."""
    if not values:
        return (0.0, 0.0, 0.0, 0.0, 0.0)
    total = float(sum(values))
    return (total, float(min(values)), float(max(values)), total / len(values), float(statistics.median(values)))


def _gaps(blocks: list[int]) -> list[float]:
    """Block differences between consecutive transactions (blocks sorted ascending)."""
    ordered = sorted(blocks)
    return [float(b - a) for a, b in zip(ordered, ordered[1:])]


def select_window(address: str, txs: list[TxRecord], observed_at_block: int | None = None,
                  window_blocks: int = WINDOW_BLOCKS, max_txs: int = MAX_TXS) -> tuple[list[TxRecord], int | None, bool]:
    """Return (kept transactions newest first, anchor block, truncated) for one address.

    Keeps transactions that touch the address, are at or before observed_at_block (if given),
    and fall in the window ending at the anchor. Duplicate txids are dropped.
    """
    seen: dict[str, TxRecord] = {}
    for tx in txs:
        if observed_at_block is not None and tx.block_height > observed_at_block:
            continue
        if any(a == address for a, _ in tx.inputs) or any(a == address for a, _ in tx.outputs):
            seen[tx.txid] = tx
    if not seen:
        return [], None, False
    anchor = max(tx.block_height for tx in seen.values())
    in_window = [tx for tx in seen.values() if tx.block_height >= anchor - window_blocks + 1]
    in_window.sort(key=lambda tx: (tx.block_height, tx.txid), reverse=True)
    return in_window[:max_txs], anchor, len(in_window) > max_txs


def compute_wallet_features(address: str, txs: list[TxRecord], observed_at_block: int | None = None,
                            window_blocks: int = WINDOW_BLOCKS, max_txs: int = MAX_TXS,
                            week_blocks: int = WEEK_BLOCKS) -> dict[str, float]:
    """Compute FEATURE_NAMES for one address, plus anchor_block (None when nothing is in range)."""
    kept, anchor, truncated = select_window(address, txs, observed_at_block, window_blocks, max_txs)
    features: dict[str, float] = {name: 0.0 for name in FEATURE_NAMES}
    features["anchor_block"] = anchor
    if not kept:
        return features

    transacted, sent_values, received_values, fees, fee_shares = [], [], [], [], []
    fee_rates, input_counts, output_counts = [], [], []
    all_blocks, sent_blocks, received_blocks, days = [], [], [], set()
    counterparty_txs: dict[str, int] = {}
    round_received = round_payment = self_change = 0
    week_txs, week_received, week_sent, week_counterparties = 0, 0, 0, set()
    for tx in kept:
        sent = sum(v for a, v in tx.inputs if a == address)
        received = sum(v for a, v in tx.outputs if a == address)
        is_sender = any(a == address for a, _ in tx.inputs)
        is_receiver = any(a == address for a, _ in tx.outputs)

        transacted.append((sent + received) / SATS_PER_BTC)
        fees.append(tx.fee_sats / SATS_PER_BTC)
        fee_shares.append(tx.fee_sats / tx.input_value_sats if tx.input_value_sats > 0 else 0.0)
        fee_rates.append(tx.fee_sats / tx.vsize if tx.vsize > 0 else 0.0)
        input_counts.append(float(tx.input_count))
        output_counts.append(float(tx.output_count))
        all_blocks.append(tx.block_height)
        days.add(tx.block_time.date())
        if is_sender:
            sent_values.append(sent / SATS_PER_BTC)
            sent_blocks.append(tx.block_height)
            paid: dict[str, int] = {}
            for a, v in tx.outputs:
                if a != address:
                    paid[a] = paid.get(a, 0) + v
            round_payment += any(v > 0 and v % ROUND_SATS == 0 for v in paid.values())
            self_change += is_receiver
        if is_receiver:
            received_values.append(received / SATS_PER_BTC)
            received_blocks.append(tx.block_height)
            round_received += received > 0 and received % ROUND_SATS == 0

        # Counterparties: who received from this wallet, and who paid it, in this transaction.
        others: set[str] = set()
        if is_sender:
            others.update(a for a, _ in tx.outputs)
        if is_receiver:
            others.update(a for a, _ in tx.inputs)
        others.discard(address)
        for other in others:
            counterparty_txs[other] = counterparty_txs.get(other, 0) + 1
        if tx.block_height >= anchor - week_blocks + 1:
            week_txs += 1
            week_received += received
            week_sent += sent
            week_counterparties |= others

    n_sent, n_received = len(sent_blocks), len(received_blocks)
    features.update(
        round_received_share=round_received / n_received if n_received else 0.0,
        round_payment_share=round_payment / n_sent if n_sent else 0.0,
        self_change_share=self_change / n_sent if n_sent else 0.0,
        total_txs_1w=float(week_txs),
        btc_received_total_1w=week_received / SATS_PER_BTC,
        btc_sent_total_1w=week_sent / SATS_PER_BTC,
        num_counterparties_1w=float(len(week_counterparties)),
    )
    features.update(
        total_txs=float(len(kept)),
        num_txs_as_sender=float(len(sent_blocks)),
        num_txs_as_receiver=float(len(received_blocks)),
        lifetime_in_blocks=float(max(all_blocks) - min(all_blocks)),
        blocks_since_first_sent=float(anchor - min(sent_blocks)) if sent_blocks else -1.0,
        blocks_since_first_received=float(anchor - min(received_blocks)) if received_blocks else -1.0,
        num_active_days=float(len(days)),
        num_counterparties=float(len(counterparty_txs)),
        num_addr_transacted_multiple=float(sum(1 for n in counterparty_txs.values() if n > 1)),
        window_truncated=1.0 if truncated else 0.0,
    )
    families = {
        "btc_transacted": transacted,
        "btc_sent": sent_values,
        "btc_received": received_values,
        "fees": fees,
        "fees_as_share": fee_shares,
        "blocks_btwn_txs": _gaps(all_blocks),
        "blocks_btwn_input_txs": _gaps(sent_blocks),
        "blocks_btwn_output_txs": _gaps(received_blocks),
        "transacted_w_address": [float(n) for n in counterparty_txs.values()],
        "fee_rate": fee_rates,
        "tx_inputs": input_counts,
        "tx_outputs": output_counts,
    }
    for family, values in families.items():
        for suffix, value in zip(STAT_SUFFIXES, _stats(values)):
            features[f"{family}_{suffix}"] = value
    return features


def window_counterparties(address: str, txs: list[TxRecord], observed_at_block: int | None = None,
                          window_blocks: int = WINDOW_BLOCKS, max_txs: int = MAX_TXS) -> tuple[set[str], set[str]]:
    """(payers, payees) over the same kept transactions as compute_wallet_features.

    Payers paid the wallet (inputs of its receiving transactions), payees were paid by it
    (outputs of its sending transactions). The wallet itself is never included.
    """
    kept, _, _ = select_window(address, txs, observed_at_block, window_blocks, max_txs)
    payers: set[str] = set()
    payees: set[str] = set()
    for tx in kept:
        if any(a == address for a, _ in tx.inputs):
            payees.update(a for a, _ in tx.outputs)
        if any(a == address for a, _ in tx.outputs):
            payers.update(a for a, _ in tx.inputs)
    payers.discard(address)
    payees.discard(address)
    return payers, payees


def exposure_features(payers: set, payees: set, illicit: set, licit: set) -> dict[str, float]:
    """How many counterparties are on known-label lists, as counts and shares.

    Works on any id type (address text live, fingerprints in training) as long as the
    counterparty sets and label sets use the same one. Only use labels known at scoring time.
    """
    def share(count: int, total: int) -> float:
        return count / total if total else 0.0

    counts = {
        "illicit_payers": len(payers & illicit), "illicit_payees": len(payees & illicit),
        "licit_payers": len(payers & licit), "licit_payees": len(payees & licit),
    }
    return {
        **{name: float(value) for name, value in counts.items()},
        "illicit_payer_share": share(counts["illicit_payers"], len(payers)),
        "illicit_payee_share": share(counts["illicit_payees"], len(payees)),
        "licit_payer_share": share(counts["licit_payers"], len(payers)),
        "licit_payee_share": share(counts["licit_payees"], len(payees)),
    }


def network_context(blocks: list[BlockStat], anchor_block: int, fees_mean_btc: float,
                    span: int = NETWORK_BLOCKS) -> dict[str, float]:
    """Network activity in the `span` blocks ending at the anchor, and the wallet's fee against it.

    Missing blocks are skipped, so pass all of them for exact numbers.
    """
    chosen = [b for b in blocks if anchor_block - span + 1 <= b.height <= anchor_block]
    tx_count = sum(b.tx_count for b in chosen)
    non_coinbase = sum(max(b.tx_count - 1, 0) for b in chosen)
    fees = sum(b.total_fees_sats for b in chosen)
    avg_fee_btc = fees / non_coinbase / SATS_PER_BTC if non_coinbase else 0.0
    return {
        "net_tx_count_144": float(tx_count),
        "net_avg_fee_btc_144": avg_fee_btc,
        "fee_vs_network_mean": fees_mean_btc / avg_fee_btc if avg_fee_btc else 0.0,
    }


async def fetch_window_transactions(provider, address: str, window_blocks: int = WINDOW_BLOCKS,
                                    max_txs: int = MAX_TXS, page_size: int = 25) -> list[TxRecord]:
    """Page through an address's confirmed history (newest first) until the window is covered.

    Stops once a transaction is older than the window, or once max_txs + 1 transactions are
    in the window (enough to know the window was truncated). Uses the live provider's
    get_address_transactions, so mempool.space rate limits apply: at most five requests.
    """
    records: list[TxRecord] = []
    anchor: int | None = None
    after_txid: str | None = None
    while True:
        page = await provider.get_address_transactions(address, after_txid=after_txid)
        confirmed = [r for r in (tx_record_from_summary(tx) for tx in page) if r is not None]
        for record in confirmed:
            if anchor is None:
                anchor = record.block_height
            if record.block_height < anchor - window_blocks + 1:
                return records
            records.append(record)
            if len(records) > max_txs:
                return records
        if len(confirmed) < page_size:
            return records
        after_txid = confirmed[-1].txid


async def live_network_context(provider, anchor_block: int, fees_mean_btc: float,
                               span: int = NETWORK_BLOCKS) -> dict[str, float]:
    """network_context() from mempool.space block summaries (15 blocks per request, 10 requests)."""
    blocks: list[BlockStat] = []
    height = anchor_block
    while height > anchor_block - span:
        page = await provider.get_block_stats(height)
        if not page:
            break
        blocks.extend(page)
        height = min(b.height for b in page) - 1
    return network_context(blocks, anchor_block, fees_mean_btc, span)


async def live_wallet_features(provider, address: str, with_network: bool = True,
                               illicit: set[str] | None = None, licit: set[str] | None = None) -> dict[str, float]:
    """Features for one address as it looks now, using the same rules as the training data.

    About 15 mempool.space requests with the network context, at most 5 without it.
    Pass known illicit/licit address sets to get the exposure features.
    """
    txs = await fetch_window_transactions(provider, address)
    features = compute_wallet_features(address, txs)
    features.update(address_type_features(address))
    if illicit is not None or licit is not None:
        payers, payees = window_counterparties(address, txs)
        features.update(exposure_features(payers, payees, illicit or set(), licit or set()))
    if with_network and features["anchor_block"] is not None:
        features.update(await live_network_context(provider, features["anchor_block"], features["fees_mean"]))
    return features
