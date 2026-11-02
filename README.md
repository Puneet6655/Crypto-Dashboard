# Crypto-Dashboard

Payments Intelligence Dashboard (student project).

- `frontend/`: React + Vite dashboard (Investigate, Cases, Live alerts, Models, Status, Sources pages).
- `backend/`: Python FastAPI app: live Bitcoin, Ethereum, Base and Solana lookups, threat-list checks,
  saved cases, and offline-benchmark models with SHAP explanations. `backend/training/` builds the
  training data and models.

**New to the project? Read [docs/README.md](docs/README.md)** (setup, architecture, data sources and
licences, ML pipeline, model dossiers, milestone status and known gaps).

## Quick start (Windows)

Double-click `run.bat` (or type `run.bat` in a terminal opened in this folder). It sets up the Python environment and frontend packages on the first run, starts the backend (http://localhost:8000) and frontend (http://localhost:5173) in two windows, and opens the dashboard. Close the two windows to stop.

## Run locally (Windows PowerShell)

Frontend:

```
cd frontend
npm install
npm run dev
```

Backend (one virtual environment in the repository root, as `run.bat` uses):

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt -r backend\requirements-ml.txt
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

## Save live data to Neon Postgres (optional)

The backend saves every live lookup (address totals, transactions, provider health checks) and every investigation to Postgres when `DATABASE_URL` is set. Tables are created automatically on start (see `backend/app/schema.sql`). Without it, everything still works but nothing is saved.

Create `backend/.env` once (it is gitignored, so the password stays on your PC):

```
cd backend
"DATABASE_URL=$(npx neonctl connection-string --project-id crimson-snow-79733119 --pooled)" | Out-File .env -Encoding ascii
```

Check it at http://localhost:8000/api/db/health. Saved cases: `GET /api/investigations`, `GET /api/investigations/{id}`, `GET /api/bitcoin/address/{address}/history`.

## Backup data sources (automatic failover)

Bitcoin lookups try mempool.space first and blockstream.info second (`BTC_PROVIDERS` in `backend/.env`). When a source answers with a rate limit (HTTP 429), an error or a timeout, the next one answers instead and the failing one gets no requests while it cools down (30 s after a rate limit, doubling to 10 min; 10 s after an error, up to 5 min). "Not found" answers are final. Recent answers are cached (30 s; confirmed transactions for a day), so repeated lookups cost no requests. Both sources use the same Esplora API and the same conversion code, so the data and the wallet features do not depend on which one answered; `python -m scripts.check_provider_parity` (from `backend/`) checks this on live addresses.

Every response still names the source that answered (`provenance.provider`), and so do saved rows and investigation evidence. `GET /api/bitcoin/providers` shows which sources are ready or cooling down, their last errors and recent fallbacks (`?check=true` also pings each one).

Blockchair was left out: it blocked keyless requests from this PC (HTTP 430).

## Ethereum and Base lookups (Etherscan, Alchemy)

With `ETHERSCAN_API_KEY` set in `backend/.env`, Crypto Investigation also accepts Ethereum addresses and transaction hashes. Alchemy support (Ethereum backup and Base) is built but off until `ALCHEMY_ENABLED=true` is set next to `ALCHEMY_API_KEY`. When on, Ethereum tries Etherscan first and Alchemy second, with the same cooldowns as Bitcoin (`GET /api/ethereum/providers`). Base uses Alchemy only, because Etherscan's free plan does not cover it (`GET /api/base/providers`); Base must be enabled for the app in Alchemy's dashboard. Alchemy's free plan allows 30 million compute units a month and 300 a second; its transfer call costs 120, so the backend spaces calls by cost. Alchemy needs no attribution. Its address history comes from two pairs of transfer calls (sent and received, up to 1,000 each): one pair for ETH (normal and internal transfers) and one for tokens (ERC-20 and NFTs), so token spam cannot push older ETH transactions out of the 1,000-record page. A full address lookup costs 480 compute units. Those lists carry no fees or success flags, which the investigation says when it happens. An address investigation asks for as much as one Etherscan call returns (1,000 records, checked with a free key) of each kind: normal transactions, ERC-20 transfers, internal transfers, and ERC-721 and ERC-1155 NFT transfers. Findings (ETH in and out, fees, counterparties, top tokens, failed transactions) and the sanctions check cover everything fetched. The graph shows up to `MAX_GRAPH_NODES` nodes, sanctioned counterparties first. A transaction investigation shows its status, fee, internal transfers and the token transfers in its receipt. Every address is checked against the OFAC list of sanctioned Ethereum-format addresses. Bitcoin APIs have fixed page sizes (25 transactions per call; 50 on mempool.space's first page), so there is nothing to raise there. The free key allows 3 calls per second and 100,000 a day: the backend spaces its calls to stay under that and caches answers. If the daily limit is reached it stops calling Etherscan for an hour. The key is never saved or shown: evidence URLs and log lines leave it out. API: `GET /api/ethereum/address/{address}`, `/address/{address}/transactions`, `/tx/{hash}`, `/providers`. OFAC lists Ethereum-format addresses without naming a chain, so a match on Base is flagged with that caveat.

## Holdings, NFTs and live alerts (Alchemy)

These need `ALCHEMY_ENABLED=true` and `ALCHEMY_API_KEY` in `backend/.env`.

- **Holdings now**: clicking an Ethereum or Base address in the graph shows its ETH and ERC-20 balances with USD values (Alchemy Token and Prices APIs, about 600 compute units, cached 5 minutes). Tokens Alchemy has no price for are listed last; they are often spam. API: `GET /api/ethereum/address/{address}/tokens` (and `/api/base/...`).
- **NFTs**: the "Load NFTs" button lists the newest 100 NFTs held, with floor prices and Alchemy's spam flag, which often marks phishing airdrops (480 compute units). API: `GET /api/ethereum/address/{address}/nfts`.
- **Live alerts**: the API keeps one WebSocket open to Alchemy and subscribes to `alchemy_minedTransactions`, filtered to the Ethereum addresses in open cases (first) and the OFAC list of sanctioned Ethereum-format addresses, up to 500 addresses. Each new transaction sent by or to one of them becomes an alert on the **Live alerts** page and a pop-up. The watchlist is re-read every 10 minutes. Alchemy bills subscriptions by data sent (0.04 compute units per byte, about 40 to 80 per alert), so only matching transactions cost anything; the watcher never subscribes without an address filter, because an unfiltered stream of all Ethereum transactions would use the free month in under a day. An address with more than 20 alerts in an hour (an exchange or token contract, which can produce about 100 a minute) is paused for a day and shown as paused, so one busy address cannot use up the month. Alerts are kept in memory (newest 200). Turn alerts off with `LIVE_ALERTS_ENABLED=false`, or stop watching OFAC addresses with `LIVE_ALERTS_WATCH_OFAC=false`. API: `GET /api/alerts`.

## Solana lookups (Alchemy)

With `ALCHEMY_ENABLED=true` (and Solana enabled for the app in Alchemy's dashboard), Crypto Investigation also accepts Solana addresses and transaction signatures. An address investigation reads the newest 1,000 signatures (one call, 40 compute units) and the newest 40 transactions in full (40 each, about 1,700 in all), then shows SOL and SPL token transfers in the graph and screens every counterparty against the OFAC list of sanctioned Solana addresses. Token transfers are drawn between the wallets that own the token accounts. Amounts and counterparties come from those 40 transactions; counts and dates use all signatures. Clicking a Solana address shows its SOL and token balances. Swaps and other program calls appear only as the plain transfers they make. API: `GET /api/solana/address/{address}`, `/address/{address}/signatures`, `/address/{address}/tokens`, `/tx/{signature}`, `/providers`.

## Sanctions, ransomware and scam-website lists

The backend downloads these public lists into `backend/data/raw/indicators/` (gitignored) on start, then re-checks hourly and refreshes any list older than a day:

- OFAC sanctioned addresses for Bitcoin, Ethereum-format and Solana (0xB10C's nightly extraction of OFAC's SDN list)
- Ransomwhere ransomware payment addresses (Bitcoin; mostly 2016-2021, newest submission October 2024)
- CryptoScamDB blacklist of scam and phishing websites (about 9,900, with about 4,000 payment addresses they used) and whitelist of genuine crypto websites (about 90), from github.com/CryptoScamDB. **No licence**: the data is only downloaded for local lookups. Do not commit it, copy it into reports or use it for training. The blacklist was last changed in June 2022 and the whitelist in April 2021, so newer scams are missing. Needs PyYAML (in `requirements.txt`).
- Newer scam-website lists (counts from 31 October 2026):
  - MetaMask eth-phishing-detect (DBAD Public License 1.2): phishing blacklist (about 114,000 sites plus 1,000 single pages on sites.google.com) and whitelist (57 names MetaMask reviewed and exempts from its look-alike check, plus the 8 names it checks look-alikes against). One 3.3 MB file.
  - ScamSniffer (GPL-3.0): about 344,000 crypto scam domains, published with a 7-day delay (9.1 MB).
  - Polkadot{.js} phishing (Apache-2.0): deny list of about 54,000 sites, and an allow list of 29 shared hosting platforms such as github.io and vercel.app (1.4 MB).
  - Phishing.Database (MIT): about 386,000 active phishing domains of every kind, not crypto-specific, so weighted lower (11 MB).
  - Tranco top 100,000 sites (a 9.7 MB zip of the top million): popularity only, never proof a site is safe. Its inputs include CC BY-NC data, so internal, non-commercial use only.

  None of the website lists goes into training labels. Together they hold about 1 million names, which take about 120 MB of memory once loaded.

Each list is saved with a `.meta.json` file (URL, download time, SHA-256, address count, when the list itself last changed). A download that fails, has the wrong shape or loses more than half its addresses is rejected and the previous copy is kept. `bitcoin_labels.csv` in the same folder lists every Bitcoin address with its source, family and dates, for training (CryptoScamDB addresses are left out because of the missing licence).

Every investigation checks each address in its graph for an exact match ("Why Flagged?" tab). Matches appear as findings with the list snapshot as evidence. No match only means "not in this snapshot", not "safe". Bitcoin, Ethereum and Base addresses are also checked against the payment addresses CryptoScamDB reported for scam sites.

In **Scam & Early Warning**, the **Indicator rules** method checks a website (`url_domain`) without visiting it: is it on the CryptoScamDB blacklist (the site, a parent domain such as `scam.example` for `login.scam.example`, or a single listed page on a shared host such as medium.com), is it on the whitelist, does its name imitate a whitelisted site (same name with another ending, a one- or two-letter typo, or containing the name, e.g. `coinbase-support.net`), and does it use international characters (punycode). The look-alike check is a warning sign only; it flags about half the phishing sites on the blacklist and also some genuine sites. The graph shows the site, the payment addresses reported for it and the genuine site it imitates. For a Bitcoin address the method checks the lists only, without fetching transactions.

The same check covers every website list. Each list that matches is shown with its title and licence ("Why Flagged?" tab, and "blacklisted by" in the details panel). The rules are:
- A blacklist match always wins over an allowlist or Tranco.
- A site on an allowlist and on no blacklist is reported as known legitimate (CryptoScamDB whitelist) or reviewed (MetaMask whitelist).
- Parent domains match, except shared hosting platforms. A blacklist that names a platform itself (ScamSniffer and Phishing.Database list sites.google.com) is ignored for it, so the platform's other sites are not flagged.
- A site on a shared platform gets no credit for the platform's Tranco rank. A popular parent domain with 20 or more blacklisted subdomains, such as the site builder yolasite.com, counts as a shared platform here.

From `backend/`: `..\.venv\Scripts\python.exe -m app.intelligence refresh` (or `status`, `check <address>`, or `check-site <url>`). API: `GET /api/indicators/status`, `GET /api/indicators/check/{address}`.

## Graph measures (NetworkX)

The **Graph measures** method runs a live lookup, then measures the graph it drew with NetworkX:
betweenness (bridges between groups of wallets; drawn as bigger nodes), PageRank on the money flow
(where value ends up) and degree. Each node's details show the values, and findings name the main
bridges. The measures describe only the graph shown (first page of transactions, node limit).

Clicking a Bitcoin address also offers its **transaction history**, page by page, beyond the first
page the graph shows (`GET /api/bitcoin/address/{address}/transactions?after_txid=...`).

## Models (offline benchmark)

The **Models** page compares a logistic-regression baseline, Random Forest and XGBoost, each with and
without hop features, on one fixed Elliptic++ split (train steps 1-34, threshold on 31-34, test
35-49, three seeds), shows the alert threshold re-picked on 2017-2019 data, and scores any address
from the benchmark dataset with a SHAP explanation. In Investigate, the **Random Forest** and
**XGBoost** methods do the same and show the SHAP chart under "Why Flagged?". Live addresses cannot be
scored yet, because live lookups cannot build the hop features; the dashboard says so. Models are not
in git: build them with `..\.venv\Scripts\python.exe -m training.model_registry` from `backend/`
(see [docs/ML_PIPELINE.md](docs/ML_PIPELINE.md)). API: `GET /api/models`, `POST /api/models/score`.

## Deploy the frontend to Vercel

Import this repository at https://vercel.com/new and set **Root Directory** to `frontend`.
Vercel detects Vite and uses `npm run build` with output folder `dist`.

## Accounts, roles and pages

The dashboard has a side panel with these pages: **Investigate** (the original lookup and graph), **Cases**,
**Status** (live lookup sources, the Neon database, sanction-list dates), **Sources** (acknowledgements),
**BigQuery refresh** (under development) and, for admins, **Users**.

Everyone signs in. New sign-ups are **viewers**: they can use every page but change nothing. An admin can
make someone an **analyst** (creates cases and adds addresses and transactions to them) or an **admin**
(also edits Sources, runs BigQuery estimates, deletes cases and manages users). Every change is in the
audit log on the Users page.

Create the first admin from `backend/` (the password is typed, never shown or stored in plain text):

```powershell
..\.venv\Scripts\python.exe -m app.auth create-admin you@example.com --name "Your Name"
```

Other commands: `set-role EMAIL viewer|analyst|admin`, `reset-password EMAIL`, `disable EMAIL`, `enable EMAIL`, `list`.

Passwords are stored in Neon only as salted scrypt hashes; sign-in tokens only as SHA-256 hashes. Five wrong
passwords for one email (or twenty from one address) lock sign-in for 15 minutes.

To add an address or transaction to a case, right-click it anywhere (graph, details panel, findings) or use
**Add to case**. Your own page icons go in `frontend/public/icons/` (prompts in `frontend/ICON_PROMPTS.md`).
