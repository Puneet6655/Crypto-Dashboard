# Data sources and their limits

Every source the dashboard or the training pipeline uses, what it costs, and what its licence
allows. The Sources page in the dashboard shows the same acknowledgements to users.

## Live blockchain data

| Source | Used for | Key | Limits |
| --- | --- | --- | --- |
| mempool.space | Bitcoin addresses, transactions, block stats (first choice) | none | Public, rate-limited (HTTP 429). First address page returns 50 confirmed transactions, later pages 25 |
| blockstream.info (Esplora) | Bitcoin backup | none | 25 transactions per page; no `/v1/blocks`, so block-based network features come from mempool.space only |
| Etherscan API V2 | Ethereum addresses and transactions (first choice) | `ETHERSCAN_API_KEY` | Free key: 3 calls/s, 100,000/day; list calls return at most 1,000 rows each. Base is not in the free plan. The backend paces calls and pauses an hour after the daily limit |
| Alchemy | Ethereum backup, Base, Solana, holdings, NFTs, live alerts | `ALCHEMY_API_KEY` + `ALCHEMY_ENABLED=true` | Free plan: 30 million compute units a month, 300 a second. Off by default |

Rejected: Blockchair blocked keyless requests from the development PC (HTTP 430 "IP temporarily
blacklisted") on the first call.

Failover (`app/providers/failover.py`): a rate limit cools the source down for 30 s, doubling to
10 min; an error or timeout for 10 s up to 5 min; "not found" is final. Answers are cached (30 s;
confirmed transactions a day). `python -m scripts.check_provider_parity` (from `backend/`) checks
that mempool.space and blockstream.info give identical features (8/8 on 30 Oct 2026).

### Alchemy

Costs per call (compute units, CU): asset transfers 120, so a full Ethereum address lookup is about
480; holdings about 600 (cached 5 min); NFTs 480; Solana signatures 40 and one full transaction 40.
Live alerts:
- Ethereum: one WebSocket subscription (`alchemy_minedTransactions`) filtered to at most 500
  addresses (case and watchlist addresses first, then OFAC). Billed by data sent, about 40-80 CU per
  alert. It never subscribes without an address filter. An address with more than 20 alerts an hour
  (an exchange or token contract) is paused for a day. Never test with busy real addresses (the USDT
  contract), because each alert costs CU.
- Solana: polled with `getSignaturesForAddress`, at most one call per 30 s, so at most about 3.5
  million CU a month however long the list is.
- Bitcoin: polled from mempool.space (blockstream backup), no Alchemy cost.
- Watchlist caps (table `alert_watch_addresses`): Ethereum 100, Solana 20, Bitcoin 50. Adding needs
  the analyst role.

The Alchemy key on the original PC has Base disabled (HTTP 403); enable Base for the app in Alchemy's
dashboard to use Base lookups.

## Threat lists (downloaded to `backend/data/raw/indicators/`, gitignored)

Refreshed automatically when older than a day. Each list has a `.meta.json` (URL, download time,
SHA-256, record count, when the list itself last changed). "Training" says whether the list may go
into training labels (`bitcoin_labels.csv`).

| List | Licence | Training | Notes |
| --- | --- | --- | --- |
| OFAC sanctioned addresses, Bitcoin / Ethereum-format / Solana (0xB10C's nightly extraction) | OFAC data is US government work; extractor MIT | yes | 532 Bitcoin addresses; no dates, so not usable as time-aware training labels or hop seeds |
| Ransomwhere | Crowdsourced; check the Zenodo record before redistributing | yes | 11,186 Bitcoin addresses, mostly 2016 (Locky); newest submission Oct 2024 |
| CryptoScamDB blacklist and whitelist | **No licence** (all rights reserved) | **no** | Local lookups only. Never commit it, copy it into reports or train on it. Last changed 2022 (blacklist), 2021 (whitelist) |
| MetaMask eth-phishing-detect | DBAD 1.2 (permissive) | no | About 114,000 phishing sites plus a reviewed whitelist |
| ScamSniffer | GPL-3.0 | no | About 344,000 scam domains, published with a 7-day delay |
| Polkadot{.js} phishing | Apache-2.0 | no | Deny list (about 54,000) and an allow list of shared hosting platforms |
| Phishing.Database | MIT | no | About 386,000 active phishing domains, not crypto-specific, weighted lower |
| Tranco top 100,000 | Tranco; inputs include CC BY-NC data | no | Internal, non-commercial use only. Popularity, never proof of safety |

A match is an exact match against a dated snapshot. No match only means "not in this snapshot",
which every result says. Rules for website matching (parent domains, shared hosting platforms,
look-alikes) are in the main README.

## Training data

| Source | What | Licence / cost |
| --- | --- | --- |
| Elliptic++ (github.com/git-disl/EllipticPlusPlus) | 822,942 wallet addresses over 49 time steps (Jan 2016 to Oct 2017), labelled illicit / licit / unknown (unknown is not licit) | No licence stated in the repository: cite Elmougy and Liu (KDD '23), confirm use with the supervisor, never commit the CSVs. Put them in `backend/data/raw/elliptic_pp/` |
| BigQuery `bigquery-public-data.crypto_bitcoin` | Every Bitcoin transaction, used to rebuild live-compatible features for Elliptic++ wallets and two later years (steps 50-101, to Sep 2019), and the transaction store for hop features | Free tier 1 TB of query a month. The full pull used about 174 GB; the hop-feature transaction store about 188 GB; a 6-day recent pull 1.7 GB. Every query is dry-run and capped (`training/bq_common.run_query`) |
| Elliptic2 (MIT-IBM, on Kaggle) | 121,810 labelled subgraphs of Elliptic's private address clusters | Needs your own Kaggle account; anonymised features, so not live-compatible. Processed copy on the USB drive `D:\elliptic2\` |

BigQuery cost rules learned the hard way: the tables are views partitioned by
`block_timestamp_month` and clustered by `block_number`, so always filter on block numbers (cuts
bytes about 60 times). Referencing the same table twice in one query lost the pruning (1 GB became
15 GB): read each table once per query. A rerun within 24 hours reuses BigQuery's stored results.
Do not create datasets or tables in the Google project; the pipeline keeps everything local.

The Google service-account key is `backend/data/certain-voyager-512206-j5-*.json` on the original
PC (gitignored). Never print or commit it.
