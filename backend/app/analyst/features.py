"""Plain-words meanings of the model's features, so the analyst explains SHAP drivers correctly.

The definitions follow app/features/wallet_window.py (window features) and training/hop_check.py
(multi-hop features). A feature not listed here gets no meaning, and the model is told to say so.
"""
from __future__ import annotations

WINDOW = "in the scoring window (about four weeks, newest 100 transactions at most)"

# Families that come as _total, _min, _max, _mean and _median: the value of each transaction or gap.
FAMILIES = {
    "btc_transacted": "BTC it moved in one transaction (sent plus received)",
    "btc_sent": "BTC it sent in one transaction",
    "btc_received": "BTC it received in one transaction",
    "fees_as_share": "fee of one of its transactions as a share of the BTC moved",
    "fees": "fee in BTC of one of its transactions",
    "blocks_btwn_txs": "blocks between one of its transactions and its next one (a block is about 10 minutes)",
    "blocks_btwn_input_txs": "blocks between one of its sending transactions and the next",
    "blocks_btwn_output_txs": "blocks between one of its receiving transactions and the next",
    "transacted_w_address": "number of transactions it had with one counterparty",
    "fee_rate": "fee rate (sat/vB) of one of its transactions",
    "tx_inputs": "number of inputs in one of its transactions",
    "tx_outputs": "number of outputs in one of its transactions",
}
SUFFIXES = {
    "total": "Sum over all its transactions of the {}",
    "min": "Smallest single {} (one extreme case, not the typical pattern)",
    "max": "Largest single {} (one extreme case, not the typical pattern)",
    "mean": "Average {}",
    "median": "Median {}",
}

PLAIN = {
    "total_txs": f"Number of its transactions {WINDOW}",
    "num_txs_as_sender": f"Number of transactions in which it sent BTC, {WINDOW}",
    "num_txs_as_receiver": f"Number of transactions in which it received BTC, {WINDOW}",
    "lifetime_in_blocks": "Blocks from its first to its last transaction in the window",
    "blocks_since_first_sent": "Blocks since it first sent BTC in the window",
    "blocks_since_first_received": "Blocks since it first received BTC in the window",
    "num_active_days": "Number of days with at least one of its transactions in the window",
    "num_counterparties": "Number of different addresses it paid or was paid by in the window",
    "num_addr_transacted_multiple": "Number of counterparties it dealt with more than once",
    "window_truncated": "1 if it had more than 100 transactions in the window, so only the newest 100 were used",
    "round_received_share": "Share of its receiving transactions where it got a round amount (a whole 0.001 BTC)",
    "round_payment_share": "Share of its sending transactions that paid someone a round amount",
    "self_change_share": "Share of its sending transactions that also paid change back to itself",
    "total_txs_1w": "Number of its transactions in the last week of the window",
    "btc_received_total_1w": "BTC it received in the last week of the window",
    "btc_sent_total_1w": "BTC it sent in the last week of the window",
    "num_counterparties_1w": "Number of different counterparties in the last week of the window",
    "net_tx_count_144": "Transactions on the whole Bitcoin network in the day before its newest transaction",
    "net_avg_fee_btc_144": "Average network fee in that day",
    "fee_vs_network_mean": "Its average fee compared with the network average (1 = the same)",
    "addr_p2pkh": "1 if the address starts with 1 (legacy type)",
    "addr_p2sh": "1 if the address starts with 3 (script type)",
    "addr_bech32": "1 if the address starts with bc1 (SegWit type)",
    "illicit_payers": "Addresses with an illicit training label (Elliptic++) that paid it",
    "illicit_payees": "Addresses with an illicit training label (Elliptic++) that it paid",
    "licit_payers": "Addresses with a licit training label that paid it",
    "licit_payees": "Addresses with a licit training label that it paid",
    "illicit_payer_share": "Share of the addresses that paid it that have an illicit training label",
    "illicit_payee_share": "Share of the addresses it paid that have an illicit training label",
    "licit_payer_share": "Share of the addresses that paid it that have a licit training label",
    "licit_payee_share": "Share of the addresses it paid that have a licit training label",
    "illicit_hop_dist": "Hops to the nearest address with an illicit label (1 = direct counterparty, 6 = none within 5)",
    "licit_hop_dist": "Hops to the nearest address with a licit label (1 = direct counterparty, 6 = none within 5)",
    "illicit_within_1": "Illicit-labelled addresses it dealt with directly",
    "illicit_within_2": "Illicit-labelled addresses within 2 hops",
    "licit_within_1": "Licit-labelled addresses it dealt with directly",
    "licit_within_2": "Licit-labelled addresses within 2 hops",
    "illicit_btc_share_1hop": "Share of its BTC that came from or went to illicit-labelled addresses in the same transaction",
    "licit_btc_share_1hop": "Share of its BTC that came from or went to licit-labelled addresses in the same transaction",
    "illicit_decayed_5": "Closeness to illicit-labelled addresses up to 5 hops (each hop counts half as much)",
    "licit_decayed_5": "Closeness to licit-labelled addresses up to 5 hops (each hop counts half as much)",
    "hop2_mean_log_btc_received": "Typical BTC received by the addresses 2 hops away (log scale)",
    "hop2_mean_log_counterparties": "Typical number of counterparties of the addresses 2 hops away (log scale)",
    "hop2_mean_log_total_txs": "Typical number of transactions of the addresses 2 hops away (log scale)",
}


def meaning(feature: str) -> str | None:
    """What a model feature measures, in plain words, or None when it is not known here."""
    if feature in PLAIN:
        return PLAIN[feature]
    family, _, suffix = feature.rpartition("_")
    if family in FAMILIES and suffix in SUFFIXES:
        return SUFFIXES[suffix].format(FAMILIES[family])
    return None
