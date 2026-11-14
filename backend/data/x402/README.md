# x402 snapshot

Compact lookup tables the x402 features read (`app/x402/snapshot.py`). About 6 MB, safe to keep in git.

Built by `training/x402_snapshot.py` from two MIT-licensed sources (credit them wherever this data is shown):

- [x402analysis](https://github.com/drinkcoffee/x402analysis) by Peter Robinson: servers, services and categories,
  clients, funders, phishing tags (Blockscout metadata), phishing funds flow.
- [x402scan](https://github.com/Merit-Systems/x402scan) by Merit Systems: facilitator addresses
  (`packages/external/facilitators`).

To refresh (both are plain git clones; Peter updates his dataset now and then):

    git clone https://github.com/drinkcoffee/x402analysis ../x402analysis
    git clone --depth 1 https://github.com/Merit-Systems/x402scan ../x402scan
    python -m training.x402_snapshot --x402analysis ../x402analysis --x402scan ../x402scan

Then restart the API. Do not train models on data fetched from x402scan's own API.
