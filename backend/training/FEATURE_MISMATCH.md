# Elliptic++ features vs live mempool.space data

Short version: a model trained on Elliptic++ wallet features will give unreliable
scores on addresses looked up live through mempool.space, unless the features are
rebuilt the same way on both sides. Treat any such score as offline-only until it
has been validated on live-style data.

## Why the scores will drift

1. **Different time window.** Elliptic++ counts activity only inside its own
   snapshot: 49 time steps of about two weeks each, under two years in total (the
   original Elliptic graph). A wallet's `total_txs`, `btc_received_total`,
   `lifetime_in_blocks` and so on describe only that slice. mempool.space reports
   the address's full history up to today. The same address can show 3 transactions
   in Elliptic++ and 67,000 live. The model has never seen values that large, so
   its output is an extrapolation.
2. **Different subgraph.** Elliptic++ only includes transactions that are in the
   Elliptic sample. Degrees, counterparties and "transacted with" counts miss every
   transaction outside that sample. Live data has no such filter.
3. **Different definitions.** Even features with the same idea (fees, BTC sent,
   blocks between transactions) were computed by the dataset authors with their own
   rules: per time step or aggregated, BTC or satoshis, how change outputs and
   coinbase inputs are treated, how missing values are filled. Small rule
   differences shift the whole distribution.
4. **Features that cannot be rebuilt.** `Local_feature_*` and `Aggregate_feature_*`
   in the transaction file are anonymised: their meaning is not published, so they
   cannot be computed from mempool.space at all. `Time step` is an Elliptic index
   with no live equivalent.
5. **Different population and era.** Pre-2019 behaviour (exchanges, mixers,
   address types, fee levels) is not today's. Taproot and modern SegWit addresses
   did not exist. The share of illicit wallets also differs, so a 0.7 score means
   something different live.
6. **Paging limits.** The live lookup currently reads only the first page of
   transactions (25 or so), so any feature computed from it sees a sample, not the
   full history.

Expected effect: scores that look confident but are miscalibrated, with
errors in both directions, and silently worse on busy addresses.

## Practical fixes (most useful first)

1. **One feature function for both sides.** Do not train on the CSV feature
   columns. Use the Elliptic++ edgelists (`AddrTx_edgelist.csv`,
   `TxAddr_edgelist.csv`) plus raw transaction data (fetched from mempool.space or a
   node for the txids in the dataset) to rebuild each feature with your own Python
   function, and call that exact function on live data. Same code, same units, same
   rules.
2. **Restrict to features both sides can compute.** Drop the anonymised and
   time-step columns. Keep counts, amounts, fees, degrees and timing gaps that you
   can derive from raw transactions. `inspect_elliptic.py` already marks each
   column in the data card.
3. **Match the time window.** When scoring live, compute features only from the
   address's transactions within a window like the training one (for example the
   same number of blocks around first activity, or the most recent N blocks), not
   its whole lifetime. Record the window in the evidence.
4. **Check the shift before trusting scores.** Compare feature distributions of
   training data and a batch of live lookups (for example population stability
   index, or a classifier trained to tell the two apart). If the two are easy to
   tell apart, the score is not reliable.
5. **Calibrate and adapt.** Recalibrate probabilities (Platt or isotonic) on a small
   labelled live-style set, for example addresses from public sanctions lists and
   known exchange addresses. Simple domain adaptation helps too: reweight training
   rows to look like live data, or train on rank or log-scaled features that are
   less sensitive to window length.
6. **Show scores as offline-only until validated.** In the dashboard, label any
   model output "experimental, trained on pre-2019 Elliptic++ data", keep it as a
   separate `model_output` finding (never mixed with observations), and show which
   features were missing or out of range. Hide the number when too many inputs are
   out of the training range.
7. **Split by time and by ID.** When evaluating, train on steps 1-34 and test on
   35-49, and make sure no address appears on both sides. The data card reports
   this overlap.
