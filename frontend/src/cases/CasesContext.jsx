import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { apiFetch } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { sameItem } from "./caseItems.js";

// The list of cases, shared by every page, so any address or transaction on screen can show
// "in 2 cases" and be added to a case from wherever it appears.
const CasesContext = createContext(null);

export function CasesProvider({ children }) {
  const { signedIn, can, config } = useAuth();
  const [cases, setCases] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  // The "Add to case" menu: which item, and where on screen to open it.
  const [picker, setPicker] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setCases(await apiFetch("/api/cases"));
      setError("");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (signedIn && config?.database) refresh();
    else setCases([]);
  }, [signedIn, config?.database, refresh]);

  const createCase = useCallback(
    async ({ title, description = "", priority = "medium", items = [] }) => {
      const created = await apiFetch("/api/cases", { method: "POST", body: { title, description, priority, items } });
      await refresh();
      return created;
    },
    [refresh]
  );

  const addItem = useCallback(
    async (caseId, item) => {
      const result = await apiFetch(`/api/cases/${caseId}/items`, { method: "POST", body: item });
      await refresh();
      return result;
    },
    [refresh]
  );

  const casesContaining = useCallback(
    (item) => (item ? cases.filter((c) => c.items.some((i) => sameItem(i, item))) : []),
    [cases]
  );

  const value = useMemo(
    () => ({
      cases, loading, error, refresh, createCase, addItem, casesContaining,
      canEdit: can("analyst"),
      picker,
      openPicker: (item, position) => setPicker({ item, x: position.x, y: position.y }),
      closePicker: () => setPicker(null),
    }),
    [cases, loading, error, refresh, createCase, addItem, casesContaining, can, picker]
  );

  return <CasesContext.Provider value={value}>{children}</CasesContext.Provider>;
}

export function useCases() {
  return useContext(CasesContext);
}
