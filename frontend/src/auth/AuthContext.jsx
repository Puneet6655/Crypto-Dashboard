import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { SESSION_ENDED_EVENT, apiFetch, getToken, setToken } from "../api/client.js";

// Who is signed in, and what they may do. Roles, least to most access:
// viewer (every new sign-up) < analyst (builds cases) < admin (sources, BigQuery, users).
export const ROLE_RANK = { viewer: 0, analyst: 1, admin: 2 };
export const ROLE_LABELS = { viewer: "Viewer", analyst: "Analyst", admin: "Admin" };

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [config, setConfig] = useState(null);
  // checking: asking the API who we are | ready | offline: the API could not be reached
  const [state, setState] = useState("checking");
  const [offlineReason, setOfflineReason] = useState("");

  const check = useCallback(async () => {
    setState("checking");
    try {
      const cfg = await apiFetch("/api/auth/config");
      setConfig(cfg);
      if (getToken() || !cfg.auth_required) {
        try {
          setUser(await apiFetch("/api/auth/me"));
        } catch (err) {
          if (err.status !== 401) throw err;
          setToken("");
          setUser(null);
        }
      } else {
        setUser(null);
      }
      setState("ready");
    } catch (err) {
      setOfflineReason(err.message);
      setState("offline");
    }
  }, []);

  useEffect(() => {
    check();
  }, [check]);

  // Any API call that gets a 401 ends the session here too.
  useEffect(() => {
    const ended = () => {
      setToken("");
      setUser(null);
    };
    window.addEventListener(SESSION_ENDED_EVENT, ended);
    return () => window.removeEventListener(SESSION_ENDED_EVENT, ended);
  }, []);

  const signIn = useCallback(async (email, password) => {
    const result = await apiFetch("/api/auth/login", { method: "POST", body: { email, password } });
    setToken(result.token);
    setUser(result.user);
    return result.user;
  }, []);

  const signUp = useCallback(async (email, displayName, password) => {
    const result = await apiFetch("/api/auth/signup", {
      method: "POST",
      body: { email, display_name: displayName, password },
    });
    setToken(result.token);
    setUser(result.user);
    return result.user;
  }, []);

  const signOut = useCallback(async () => {
    try {
      await apiFetch("/api/auth/logout", { method: "POST" });
    } catch {
      // Signing out locally is enough if the API cannot be reached.
    }
    setToken("");
    setUser(null);
  }, []);

  const value = useMemo(() => {
    const signedIn = Boolean(user && user.id !== null);
    const can = (role) => signedIn && (ROLE_RANK[user.role] ?? -1) >= ROLE_RANK[role];
    return { user, config, state, offlineReason, signedIn, can, check, signIn, signUp, signOut };
  }, [user, config, state, offlineReason, check, signIn, signUp, signOut]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}
