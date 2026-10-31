import { useEffect, useState } from "react";
import { apiFetch } from "../api/client.js";

const EVM_ADDRESS_ID = /^(ethereum|base):[a-z0-9-]+:address:(0x[0-9a-f]{40})$/;
const usd = (v) => (v == null ? "no price" : v.toLocaleString(undefined, { style: "currency", currency: "USD" }));

// What an Ethereum or Base address holds now (Alchemy Token, Prices and NFT APIs).
// Tokens load with the panel; NFTs cost more (480 compute units), so they load on request.
export default function HoldingsPanel({ nodeId }) {
  const sol = SOLANA_ADDRESS_ID.exec(nodeId ?? "");
  if (sol) return <SolanaHoldings address={sol[1]} />;
  return <EvmHoldings nodeId={nodeId} />;
}

const SOLANA_ADDRESS_ID = /^solana:mainnet:address:([1-9A-HJ-NP-Za-km-z]{32,44})$/;
const KNOWN_MINTS = {
  EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v: "USDC",
  Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB: "USDT",
  So11111111111111111111111111111111111111112: "wSOL",
};

// SOL and SPL token balances held now (Alchemy Solana RPC). No USD prices for Solana yet.
function SolanaHoldings({ address }) {
  const [state, setState] = useState({ loading: true });
  useEffect(() => {
    const controller = new AbortController();
    setState({ loading: true });
    apiFetch(`/api/solana/address/${address}/tokens`, { signal: controller.signal })
      .then((data) => setState({ data }))
      .catch((err) => err.name !== "AbortError" && setState({ error: err.message }));
    return () => controller.abort();
  }, [address]);
  const d = state.data;
  return (
    <>
      <h3>Holdings now</h3>
      {state.loading && <p className="small muted">Loading balances...</p>}
      {state.error && <p className="small muted">{state.error}</p>}
      {d && (
        <>
          <p className="small">{(Number(d.balance_lamports) / 1e9).toLocaleString(undefined, { maximumFractionDigits: 9 })} SOL
            {d.tokens.length > 0 && <> · {d.tokens.length} tokens</>}</p>
          {d.tokens.length > 0 && (
            <ul className="compact-list scroll-list">
              {d.tokens.map((t) => (
                <li key={t.token_account}>
                  {t.amount ?? t.amount_raw}{" "}
                  {KNOWN_MINTS[t.mint] ?? <span className="mono small" title={t.mint}>{t.mint.slice(0, 6)}…{t.mint.slice(-4)}</span>}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </>
  );
}

function EvmHoldings({ nodeId }) {
  const match = EVM_ADDRESS_ID.exec(nodeId ?? "");
  const [tokens, setTokens] = useState({ loading: false });
  const [nfts, setNfts] = useState(null);
  const chain = match?.[1];
  const address = match?.[2];

  useEffect(() => {
    if (!address) return;
    const controller = new AbortController();
    setNfts(null);
    setTokens({ loading: true });
    apiFetch(`/api/${chain}/address/${address}/tokens`, { signal: controller.signal })
      .then((data) => setTokens({ data }))
      .catch((err) => err.name !== "AbortError" && setTokens({ error: err.message }));
    return () => controller.abort();
  }, [chain, address]);

  if (!address) return null;

  async function loadNfts() {
    setNfts({ loading: true });
    try {
      setNfts({ data: await apiFetch(`/api/${chain}/address/${address}/nfts`) });
    } catch (err) {
      setNfts({ error: err.message });
    }
  }

  const t = tokens.data;
  return (
    <>
      <h3>Holdings now</h3>
      {tokens.loading && <p className="small muted">Loading balances...</p>}
      {tokens.error && <p className="small muted">{tokens.error}</p>}
      {t && (
        <>
          <p className="small">
            {(Number(t.eth_balance_wei) / 1e18).toLocaleString(undefined, { maximumFractionDigits: 6 })} ETH
            {t.eth_value_usd != null && <> ({usd(t.eth_value_usd)})</>}
            {t.tokens.length > 0 && <> · tokens with a price: {usd(t.tokens_total_usd)}</>}
          </p>
          {t.tokens.length > 0 && (
            <ul className="compact-list scroll-list">
              {t.tokens.map((tok) => (
                <li key={tok.token_contract} className={tok.value_usd == null ? "muted" : ""}>
                  {tok.balance ?? tok.balance_raw} {tok.token_symbol ?? "?"}{" "}
                  <span className="small">{tok.value_usd != null ? usd(tok.value_usd) : "no price (often spam)"}</span>
                </li>
              ))}
            </ul>
          )}
          {t.tokens_not_shown > 0 && <p className="small muted">{t.tokens_not_shown} more tokens not shown.</p>}
        </>
      )}

      <h3>NFTs</h3>
      {!nfts && <button type="button" className="secondary small-button" onClick={loadNfts}>Load NFTs</button>}
      {nfts?.loading && <p className="small muted">Loading NFTs...</p>}
      {nfts?.error && <p className="small muted">{nfts.error}</p>}
      {nfts?.data && (
        <>
          <p className="small">
            {nfts.data.total_count ?? nfts.data.nfts.length} held
            {nfts.data.spam_count > 0 && <> · <span className="pill pill-down">{nfts.data.spam_count} flagged as spam</span></>}
            {nfts.data.total_count > nfts.data.nfts.length && <> · newest {nfts.data.nfts.length} shown</>}
          </p>
          {nfts.data.spam_count > 0 && (
            <p className="small muted">Spam NFTs are often airdropped to lure the holder to a phishing site.</p>
          )}
          <ul className="nft-grid">
            {nfts.data.nfts.map((n) => (
              <li key={`${n.token_contract}:${n.token_id}`} className={n.is_spam ? "nft-spam" : ""}
                title={`${n.collection ?? n.token_contract} #${n.token_id}`}>
                {n.image_url ? <img src={n.image_url} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <span className="nft-blank" />}
                <span className="small">{n.name ?? `#${n.token_id}`}</span>
                <span className="small muted">
                  {n.is_spam ? "spam" : n.floor_price_eth != null ? `floor ${n.floor_price_eth} ETH` : n.collection ?? ""}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
