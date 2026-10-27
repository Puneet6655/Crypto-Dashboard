// Every call from the browser to our FastAPI backend goes through this file.
// The base URL comes from a Vite environment variable so the same code works
// locally (http://localhost:8000) and on Vercel (your hosted backend URL).
// Never put secrets in VITE_ variables: they are copied into the public JavaScript.
export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

const TIMEOUT_MS = 20000;

// POST /api/investigations. Returns the result object or throws an Error whose
// message is safe to show to the analyst. Pass { signal } to let the caller
// cancel the request (the Cancel button in the loading state does this).
export async function startInvestigation({ mode, inputType, value, method }, { signal } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  // Forward the caller's cancel to our own controller, which also handles the timeout.
  const forwardCancel = () => controller.abort();
  signal?.addEventListener("abort", forwardCancel);

  let response;
  try {
    response = await fetch(`${API_BASE_URL}/api/investigations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, input_type: inputType, value: value.trim(), method }),
      signal: controller.signal,
    });
  } catch (err) {
    if (err.name === "AbortError") {
      throw new Error(`The API did not answer within ${TIMEOUT_MS / 1000} seconds.`);
    }
    throw new Error(`Cannot reach the API at ${API_BASE_URL}. Is the backend running?`);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", forwardCancel);
  }

  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(describeError(response.status, body));
  }
  return body;
}

// Turn an HTTP error into one readable sentence.
function describeError(status, body) {
  const detail = body?.detail;
  if (typeof detail === "string") return `${detail} (HTTP ${status})`;
  if (Array.isArray(detail)) {
    // Pydantic validation errors arrive as a list of {loc, msg}
    return `Invalid request: ${detail.map((d) => `${d.loc.at(-1)}: ${d.msg}`).join("; ")} (HTTP ${status})`;
  }
  return `The API returned HTTP ${status}.`;
}
