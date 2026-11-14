import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "../api/client.js";
import { useToast } from "../components/Toasts.jsx";

const POLL_SECONDS = 20;

// Asks the API for new live alerts (watched addresses moving money on Ethereum, Base, Solana or Bitcoin)
// every 20 seconds and shows a toast for each new one. The API holds the WebSocket to Alchemy,
// so the key never reaches the browser.
export function useLiveAlerts({ onOpen } = {}) {
  const toast = useToast();
  const [alerts, setAlerts] = useState([]);
  const [status, setStatus] = useState(null);
  const [statuses, setStatuses] = useState(null);
  const [unseen, setUnseen] = useState(0);
  const [pulse, setPulse] = useState(0); // goes up once per batch of new alerts (Web3 backdrop ripple)
  const lastId = useRef(0);
  const first = useRef(true);
  const openRef = useRef(onOpen);
  openRef.current = onOpen;

  const poll = useCallback(async () => {
    try {
      const body = await apiFetch(`/api/alerts?after_id=${lastId.current}&limit=200`);
      setStatus(body.status);
      setStatuses(body.statuses ?? null);
      if (!body.alerts.length) return;
      lastId.current = Math.max(lastId.current, ...body.alerts.map((a) => a.id));
      setAlerts((old) => [...body.alerts, ...old].slice(0, 200));
      // Alerts that arrived before this page opened are listed, not announced.
      if (!first.current) {
        setUnseen((n) => n + body.alerts.length);
        setPulse((n) => n + 1);
        for (const a of body.alerts.slice(0, 3)) {
          const who = a.watched[0]?.reasons[0] ?? "watched address";
          const moved = a.direction ? `${a.direction === "out" ? "sent" : "received"} ${a.amount ?? ""} ${a.unit}` : `made a ${a.unit} transaction`;
          toast(`${a.sanctioned ? "Sanctioned address" : "Watched address"} ${moved} (${who})`, {
            tone: a.sanctioned ? "error" : "info", timeout: 10000,
            action: openRef.current ? { label: "View", onClick: () => openRef.current(a) } : undefined,
          });
        }
      }
    } catch {
      // The API being briefly unreachable is shown on the Status page; try again next time.
    } finally {
      first.current = false;
    }
  }, [toast]);

  useEffect(() => {
    poll();
    const timer = setInterval(poll, POLL_SECONDS * 1000);
    return () => clearInterval(timer);
  }, [poll]);

  return { alerts, status, statuses, unseen, pulse, markSeen: () => setUnseen(0), reload: poll };
}
