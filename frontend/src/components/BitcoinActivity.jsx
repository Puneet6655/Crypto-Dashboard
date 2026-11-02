import { useEffect, useState } from "react";
import { apiFetch } from "../api/client.js";
import { formatBtc } from "../utils/bitcoinAmount.js";

const BITCOIN_ADDRESS_ID = /^bitcoin:mainnet:address:(.+)$/;

// Milestone 5 paging: the address's full transaction history, one page at a time, newest first.
// The graph shows only the first page; this list can go further back ("Load older").
export default function BitcoinActivity({ nodeId }) {
  const match = BITCOIN_ADDRESS_ID.exec(nodeId ?? "");
  const address = match?.[1];
  const [pages, setPages] = useState([]);
  const [state, setState] = useState({ status: "idle" });

  useEffect(() => {
    setPages([]);
    setState({ status: "idle" });
  }, [address]);

  if (!address) return null;
  const txs = pages.flat();
  const lastConfirmed = [...txs].reverse().find((t) => t.status === "confirmed")?.txid;

  async function load() {
    setState({ status: "loading" });
    try {
      const after = pages.length ? `?after_txid=${lastConfirmed}` : "";
      const page = await apiFetch(`/api/bitcoin/address/${address}/transactions${after}`);
      setPages((p) => [...p, page]);
      setState({ status: page.length ? "idle" : "done" });
    } catch (err) {
      setState({ status: "error", error: err.message });
    }
  }

  // Net effect on this address: what its outputs received minus what its inputs spent.
  const net = (t) =>
    t.outputs.filter((o) => o.address === address).reduce((s, o) => s + (o.value_sats ?? 0), 0) -
    t.inputs.filter((i) => i.address === address).reduce((s, i) => s + (i.value_sats ?? 0), 0);

  return (
    <>
      <h3>Transaction history{txs.length ? ` (${txs.length} loaded)` : ""}</h3>
      {txs.length > 0 && (
        <ul className="compact-list scroll-list">
          {txs.map((t) => {
            const change = net(t);
            return (
              <li key={t.txid}>
                <span className={change >= 0 ? "amount-in" : "amount-out"}>{change >= 0 ? "+" : ""}{formatBtc(change)}</span>
                <div className="small muted">
                  {t.block_time ? new Date(t.block_time).toLocaleString() : "unconfirmed"} ·{" "}
                  <a className="mono" href={`https://mempool.space/tx/${t.txid}`} target="_blank" rel="noreferrer">{t.txid.slice(0, 10)}…</a>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {state.status === "error" && <p className="small error-text">{state.error}</p>}
      {state.status === "done" ? (
        <p className="small muted">No older transactions.</p>
      ) : (
        <button type="button" className="secondary small-button" onClick={load} disabled={state.status === "loading" || (pages.length > 0 && !lastConfirmed)}>
          {state.status === "loading" ? "Loading..." : pages.length ? "Load older transactions" : "Show transaction history"}
        </button>
      )}
    </>
  );
}
