"""Facts: the named numbers a rule condition can read.

A rule pack never touches raw blockchain data. It reads facts, which the dashboard works out once per
address. Adding a fact here makes it available to every pack; the catalog below is also served at
GET /api/red-flags/facts so pack authors can see what exists.

Bitcoin facts use the same look-back window as the live model (app/features/wallet_window.py): the
address's newest confirmed transaction and the four weeks before it, at most 100 transactions. Every
fact is about that window unless its description says otherwise.

A fact is None when it cannot be worked out (for example the list it needs is not downloaded). Rules
that read it then show as "not checked" instead of "clear".
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from app.features.wallet_window import (
    SATS_PER_BTC,
    WEEK_BLOCKS,
    TxRecord,
    compute_wallet_features,
    select_window,
    window_counterparties,
)

BLOCKS_PER_DAY = 144
BLOCKS_PER_HOUR = 6
COINJOIN_EQUAL_OUTPUTS = 5  # a transaction with at least this many outputs of exactly the same value
PEEL_SMALL_SHARE = 0.1      # a 2-output spend where the smaller output is at most this share of the total


@dataclass(frozen=True)
class FactInfo:
    name: str
    chains: tuple[str, ...]
    unit: str
    description: str


CATALOG: dict[str, FactInfo] = {f.name: f for f in (
    # Activity
    FactInfo("tx_count", ("bitcoin",), "transactions", "Confirmed transactions in the window (at most 100)."),
    FactInfo("tx_count_week", ("bitcoin",), "transactions", "Transactions in the last 7 days of the window."),
    FactInfo("received_tx_count", ("bitcoin",), "transactions",
             "Transactions that paid the address and that it did not pay from (change is not a receipt)."),
    FactInfo("sent_tx_count", ("bitcoin",), "transactions", "Transactions the address paid from."),
    FactInfo("window_truncated", ("bitcoin",), "0 or 1", "1 when the window held more than 100 transactions."),
    FactInfo("active_days", ("bitcoin",), "days", "Calendar days with at least one transaction."),
    FactInfo("wallet_span_days", ("bitcoin",), "days",
             "Days between the first and last transaction in the window (a new wallet has a small value)."),
    FactInfo("first_seen_days_ago", ("bitcoin",), "days",
             "Days from the first transaction in the window to the newest one. Equals the wallet's age when "
             "the window is not truncated and the wallet is younger than four weeks."),
    # Amounts (BTC)
    FactInfo("received_btc", ("bitcoin",), "BTC", "Total received from others in the window (change left out)."),
    FactInfo("sent_btc", ("bitcoin",), "BTC", "Total paid to others in the window (change back to itself left out)."),
    FactInfo("received_btc_week", ("bitcoin",), "BTC", "Received in the last 7 days of the window."),
    FactInfo("sent_btc_week", ("bitcoin",), "BTC", "Sent in the last 7 days of the window."),
    FactInfo("max_received_btc", ("bitcoin",), "BTC", "Largest single amount received."),
    FactInfo("max_sent_btc", ("bitcoin",), "BTC", "Largest single amount sent."),
    FactInfo("median_received_btc", ("bitcoin",), "BTC", "Median amount received per transaction."),
    FactInfo("largest_send_vs_median_receive", ("bitcoin",), "times",
             "Largest amount sent divided by the median amount received (many small in, one big out)."),
    FactInfo("pass_through_ratio", ("bitcoin",), "ratio",
             "Sent divided by received in the window. Close to 1 means money passes straight through."),
    FactInfo("round_received_share", ("bitcoin",), "share 0-1",
             "Share of receiving transactions with a round amount (a whole multiple of 0.001 BTC)."),
    FactInfo("round_payment_share", ("bitcoin",), "share 0-1",
             "Share of sending transactions that paid someone a round amount."),
    # Speed
    FactInfo("median_hours_to_spend", ("bitcoin",), "hours",
             "Median time from the latest payment in to each payment out. Small means funds are moved on quickly."),
    # Shape
    FactInfo("counterparties", ("bitcoin",), "addresses", "Distinct addresses it paid or was paid by."),
    FactInfo("payers", ("bitcoin",), "addresses", "Distinct addresses that paid it."),
    FactInfo("payees", ("bitcoin",), "addresses", "Distinct addresses it paid."),
    FactInfo("max_tx_inputs", ("bitcoin",), "inputs", "Most inputs in one of its transactions."),
    FactInfo("max_tx_outputs", ("bitcoin",), "outputs", "Most outputs in one of its transactions."),
    FactInfo("coinjoin_like_tx_count", ("bitcoin",), "transactions",
             f"Transactions with at least {COINJOIN_EQUAL_OUTPUTS} outputs of exactly the same value "
             "(the usual shape of a CoinJoin mixing round)."),
    FactInfo("peel_like_send_share", ("bitcoin",), "share 0-1",
             "Share of its spends with two outputs, none back to itself, where the smaller output is at most "
             f"{int(PEEL_SMALL_SHARE * 100)}% of the total (one step of a peel chain)."),
    # Lists (exact matches against the downloaded snapshots)
    FactInfo("on_sanctions_list", ("bitcoin",), "0 or 1", "1 when the address is on the OFAC SDN snapshot."),
    FactInfo("on_ransomware_list", ("bitcoin",), "0 or 1", "1 when the address is on the Ransomwhere snapshot."),
    FactInfo("on_scam_list", ("bitcoin",), "0 or 1", "1 when CryptoScamDB lists the address for a scam site."),
    FactInfo("sanctioned_counterparties", ("bitcoin",), "addresses",
             "Counterparties on the OFAC SDN snapshot."),
    FactInfo("ransomware_counterparties", ("bitcoin",), "addresses",
             "Counterparties on the Ransomwhere snapshot."),
    FactInfo("scam_counterparties", ("bitcoin",), "addresses", "Counterparties CryptoScamDB lists."),
    FactInfo("illicit_label_counterparties", ("bitcoin",), "addresses",
             "Counterparties labelled illicit in the model's known-label table (Elliptic++ and the lists)."),
    FactInfo("illicit_payer_share", ("bitcoin",), "share 0-1",
             "Share of payers labelled illicit in the known-label table."),
    # Not available yet: listed so packs can use them now and show "not checked" until a source exists.
    FactInfo("usd_near_reporting_threshold_count", ("bitcoin",), "transactions",
             "Payments worth just under a cash reporting threshold (e.g. USD 10,000). Needs a price feed: "
             "not available yet, so rules using it show as not checked."),
)}

LIST_SOURCES = {  # fact prefix -> indicator source id
    "sanction": "ofac_xbt",
    "ransomware": "ransomwhere",
    "scam": "cryptoscamdb_blacklist",
}


@dataclass
class BitcoinFacts:
    facts: dict
    # Counterparty addresses behind the list facts, for the graph and evidence: kind -> [(address, family)]
    listed_counterparties: dict[str, list[tuple[str, str | None]]]
    anchor_block: int | None
    lists_checked: dict[str, dict]  # source id -> {title, url, snapshot}


def _safe_div(a: float, b: float) -> float | None:
    return a / b if b else None


def bitcoin_facts(address: str, txs: list[TxRecord], index=None, illicit: frozenset[str] | None = None) -> BitcoinFacts:
    """Work out every Bitcoin fact for one address from its window transactions.

    index: the IndicatorIndex (None: list facts are unknown). illicit: the known-label illicit set
    (None: the label facts are unknown).
    """
    wf = compute_wallet_features(address, txs)
    kept, anchor, _ = select_window(address, txs)
    facts: dict = {name: None for name in CATALOG}
    lists_checked: dict[str, dict] = {}
    listed: dict[str, list[tuple[str, str | None]]] = {}

    if index is not None:
        for kind, source_id in LIST_SOURCES.items():
            loaded = index.loaded(source_id)
            if loaded is None:
                continue
            lists_checked[source_id] = {"title": loaded.source.title, "url": loaded.source.url,
                                        "snapshot": loaded.snapshot_label(), "retrieved_at": loaded.retrieved_at}
            on = bool(index.lookup(address, [source_id]))
            facts[{"sanction": "on_sanctions_list", "ransomware": "on_ransomware_list",
                   "scam": "on_scam_list"}[kind]] = 1 if on else 0

    if not kept:
        return BitcoinFacts(facts, listed, None, lists_checked)

    # Amounts here leave out change: a payment the address makes to itself in the same transaction is
    # neither "received" nor "sent". A receipt is a transaction it did not pay from.
    week_start = anchor - WEEK_BLOCKS + 1
    received, sent, hold_blocks = [], [], []
    received_week = sent_week = 0.0
    coinjoin = peel = 0
    last_receive_block: int | None = None
    for tx in sorted(kept, key=lambda t: (t.block_height, t.txid)):
        got = sum(v for a, v in tx.outputs if a == address)
        paid = sum(v for a, v in tx.inputs if a == address)
        if got and not paid:
            received.append(got / SATS_PER_BTC)
            received_week += got / SATS_PER_BTC if tx.block_height >= week_start else 0.0
            last_receive_block = tx.block_height
        if paid:
            net = max(paid - got, 0) / SATS_PER_BTC
            sent.append(net)
            sent_week += net if tx.block_height >= week_start else 0.0
            if last_receive_block is not None:
                hold_blocks.append(tx.block_height - last_receive_block)
            outs = [v for a, v in tx.outputs if a != address]
            if len(tx.outputs) == 2 and len(outs) == 2 and sum(outs) > 0 and min(outs) / sum(outs) <= PEEL_SMALL_SHARE:
                peel += 1
        values = [v for _, v in tx.outputs]
        if values and max(values.count(v) for v in set(values)) >= COINJOIN_EQUAL_OUTPUTS:
            coinjoin += 1

    received_total = sum(received)
    sent_total = sum(sent)
    median_received = statistics.median(received) if received else None
    facts.update(
        tx_count=int(wf["total_txs"]),
        tx_count_week=int(wf["total_txs_1w"]),
        received_tx_count=len(received),
        sent_tx_count=len(sent),
        window_truncated=int(wf["window_truncated"]),
        active_days=int(wf["num_active_days"]),
        wallet_span_days=round(wf["lifetime_in_blocks"] / BLOCKS_PER_DAY, 2),
        first_seen_days_ago=round((anchor - min(t.block_height for t in kept)) / BLOCKS_PER_DAY, 2),
        received_btc=received_total,
        sent_btc=sent_total,
        received_btc_week=received_week,
        sent_btc_week=sent_week,
        max_received_btc=max(received) if received else 0.0,
        max_sent_btc=max(sent) if sent else 0.0,
        median_received_btc=median_received,
        largest_send_vs_median_receive=(max(sent) / median_received) if sent and median_received else None,
        pass_through_ratio=_safe_div(sent_total, received_total),
        round_received_share=wf["round_received_share"],
        round_payment_share=wf["round_payment_share"],
        median_hours_to_spend=(statistics.median(hold_blocks) / BLOCKS_PER_HOUR) if hold_blocks else None,
        counterparties=int(wf["num_counterparties"]),
        max_tx_inputs=int(wf["tx_inputs_max"]),
        max_tx_outputs=int(wf["tx_outputs_max"]),
        coinjoin_like_tx_count=coinjoin,
        peel_like_send_share=_safe_div(peel, len(sent)) if sent else 0.0,
    )

    payers, payees = window_counterparties(address, txs)
    facts.update(payers=len(payers), payees=len(payees))
    others = sorted(payers | payees)
    if index is not None:
        for kind, source_id in LIST_SOURCES.items():
            if source_id not in lists_checked:
                continue
            hits = [(a, h.family) for a in others for h in index.lookup(a, [source_id])]
            listed[kind] = hits
            facts[f"{'sanctioned' if kind == 'sanction' else kind}_counterparties"] = len({a for a, _ in hits})
    if illicit is not None:
        bad = [a for a in others if a in illicit]
        listed["illicit_label"] = [(a, None) for a in bad]
        facts["illicit_label_counterparties"] = len(bad)
        facts["illicit_payer_share"] = _safe_div(len(payers & illicit), len(payers)) if payers else 0.0
    return BitcoinFacts(facts, listed, anchor, lists_checked)
