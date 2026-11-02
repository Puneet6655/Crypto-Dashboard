// Every call from the browser to our FastAPI backend goes through this file.
// The base URL comes from a Vite environment variable so the same code works
// locally (http://localhost:8000) and on Vercel (your hosted backend URL).
// Never put secrets in VITE_ variables: they are copied into the public JavaScript.
export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

const TIMEOUT_MS = 20000;
const TOKEN_KEY = "pd_session_token";

// The sign-in token lives in localStorage so a page reload keeps you signed in.
// The server only stores a hash of it, and signing out deletes it on both sides.
export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Private windows can block storage; the session then lasts until the tab closes.
  }
}

// Any call that comes back 401 means the session ended (expired, signed out elsewhere,
// or the account was turned off). AuthContext listens for this and shows the sign-in page.
export const SESSION_ENDED_EVENT = "pd:session-ended";

// fetch() with the base URL, the sign-in token, a timeout, JSON in and out,
// and errors turned into one readable sentence. Pass { signal } to allow cancelling.
export async function apiFetch(path, { method = "GET", body, signal, timeoutMs = TIMEOUT_MS } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort("timeout"), timeoutMs);
  const forwardCancel = () => controller.abort("cancel");
  signal?.addEventListener("abort", forwardCancel);

  const headers = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (err) {
    if (err.name === "AbortError" && signal?.aborted) throw err; // the caller cancelled
    if (err.name === "AbortError") throw new Error(`The API did not answer within ${timeoutMs / 1000} seconds.`);
    throw new Error(`Cannot reach the API at ${API_BASE_URL}. Is the backend running?`);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", forwardCancel);
  }

  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && token) window.dispatchEvent(new Event(SESSION_ENDED_EVENT));
    const error = new Error(describeError(response.status, data));
    error.status = response.status;
    throw error;
  }
  return data;
}

// POST /api/investigations. Returns the result object or throws an Error whose
// message is safe to show to the analyst. Pass { signal } to let the caller
// cancel the request (the Cancel button in the loading state does this).
export function startInvestigation({ mode, inputType, value, method }, { signal } = {}) {
  return apiFetch("/api/investigations", {
    method: "POST",
    body: { mode, input_type: inputType, value: value.trim(), method },
    signal,
    // Model methods load a saved model and compute SHAP on first use, which can take longer.
    timeoutMs: ["random_forest", "xgboost"].includes(method) ? 120000 : undefined,
  });
}

// Turn an HTTP error into one readable sentence.
function describeError(status, body) {
  const detail = body?.detail;
  if (typeof detail === "string") return status >= 500 ? `${detail} (HTTP ${status})` : detail;
  if (Array.isArray(detail)) {
    // Pydantic validation errors arrive as a list of {loc, msg}
    return `Invalid request: ${detail.map((d) => `${d.loc.at(-1)}: ${d.msg}`).join("; ")} (HTTP ${status})`;
  }
  return `The API returned HTTP ${status}.`;
}
