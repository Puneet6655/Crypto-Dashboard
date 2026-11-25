import { apiFetch } from "../api/client.js";

// AI analyst (Milestone 14): a local model in Ollama explains the case and answers questions.
// The backend sends it only this case's facts and checks every answer for addresses, transaction IDs and
// amounts that are not in those facts (backend/app/analyst). A local 8B model takes 10 to 60 seconds.
// provider: "ollama" (local, the default) or "claude_cli" (Claude through the Claude CLI; sends the facts to Anthropic).
const ANSWER_TIMEOUT_MS = 320000; // Claude with the whole case can take a few minutes

export function analystStatus({ signal } = {}) {
  // The server also checks the Claude CLI (two quick local commands, cached for a minute).
  return apiFetch("/api/analyst/status", { signal, timeoutMs: 20000 });
}

export function explainCase(result, { signal, provider } = {}) {
  return apiFetch("/api/analyst/explain", { method: "POST", body: { investigation: result, ...(provider ? { provider } : {}) }, signal, timeoutMs: ANSWER_TIMEOUT_MS });
}

// messages: [{ role: "user" | "assistant", content }], ending with the new question.
export function askAnalyst(result, messages, { signal, provider } = {}) {
  return apiFetch("/api/analyst/chat", { method: "POST", body: { investigation: result, messages, ...(provider ? { provider } : {}) }, signal, timeoutMs: ANSWER_TIMEOUT_MS });
}
