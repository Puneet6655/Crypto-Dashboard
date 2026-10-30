import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "../api/client.js";

// Load one API path and keep { data, error, loading } for it. reload() asks again;
// a newer answer always wins over an older one that arrives late.
export function useApi(path, { enabled = true } = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(Boolean(enabled && path));
  const [loadedAt, setLoadedAt] = useState(null);
  const [ms, setMs] = useState(null);
  const latest = useRef(0);

  const reload = useCallback(async () => {
    if (!enabled || !path) return;
    const call = ++latest.current;
    setLoading(true);
    const started = performance.now();
    try {
      const body = await apiFetch(path);
      if (call !== latest.current) return;
      setData(body);
      setError("");
    } catch (err) {
      if (call !== latest.current) return;
      setError(err.message);
    } finally {
      if (call === latest.current) {
        setMs(Math.round(performance.now() - started));
        setLoadedAt(new Date());
        setLoading(false);
      }
    }
  }, [path, enabled]);

  useEffect(() => {
    reload();
  }, [reload]);

  return { data, error, loading, reload, loadedAt, ms, setData };
}
