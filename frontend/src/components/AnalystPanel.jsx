import { useEffect, useRef, useState } from "react";
import { analystStatus, askAnalyst, explainCase } from "../services/analyst.js";

// The AI Analyst tab (Milestone 14): "Explain this case" and a chat about the case, answered by a local
// model in Ollama, or by Claude through the Claude CLI if the analyst picks it. Answers are kept per investigation
// while the page is open, so switching tabs keeps them.
const memory = new Map();

// Who answers. Kept only while the page is open (the app stores nothing else in the browser).
let pickedProvider = null;
const PROVIDERS = {
  ollama: { label: "Local model (this computer)", wait: "A local model takes 10 to 60 seconds." },
  claude_cli: { label: "Claude CLI (sends case facts to Anthropic)", wait: "Claude reads the whole case and writes a full answer: 20 seconds to a few minutes." },
};

const SUGGESTIONS = [
  "Why did this address get this score?",
  "Which counterparty looks riskiest, and why?",
  "Draft a short case note for the file.",
];

export default function AnalystPanel({ result }) {
  const id = result.investigation_id;
  const saved = memory.get(id) ?? { explanation: null, chat: [] };
  const [explanation, setExplanation] = useState(saved.explanation);
  const [chat, setChat] = useState(saved.chat);
  const [status, setStatus] = useState(null);
  const [picked, setPicked] = useState(pickedProvider);
  const [busy, setBusy] = useState(null); // "explain" | "chat" | null
  const [error, setError] = useState("");
  const [question, setQuestion] = useState("");
  const controller = useRef(null);
  const chatEnd = useRef(null);

  useEffect(() => {
    memory.set(id, { explanation, chat });
  }, [id, explanation, chat]);

  useEffect(() => {
    const abort = new AbortController();
    analystStatus({ signal: abort.signal }).then(setStatus).catch((err) => {
      if (err.name !== "AbortError") setStatus({ ollama_running: false, model_ready: false, detail: err.message });
    });
    return () => {
      abort.abort();
      controller.current?.abort();
    };
  }, []);

  useEffect(() => {
    chatEnd.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [chat.length]);

  const provider = picked ?? status?.provider ?? "ollama";
  const claude = provider === "claude_cli";
  const ready = claude ? status?.claude_cli?.ready : status?.model_ready;
  const detail = claude ? status?.claude_cli?.detail : status?.detail;
  const pick = (value) => {
    pickedProvider = value;
    setPicked(value);
  };

  async function run(kind, work) {
    controller.current?.abort();
    controller.current = new AbortController();
    setBusy(kind);
    setError("");
    try {
      return await work(controller.current.signal);
    } catch (err) {
      if (err.name !== "AbortError") setError(err.message);
      return null;
    } finally {
      setBusy(null);
    }
  }

  const explain = () => run("explain", async (signal) => {
    const answer = await explainCase(result, { signal, provider });
    if (answer) setExplanation({ ...answer, graphNodes: result.graph?.nodes?.length ?? 0 });
  });

  const ask = (text) => {
    const content = (text ?? question).trim();
    if (!content || busy) return;
    const history = [...chat.map(({ role, content: c }) => ({ role, content: c })), { role: "user", content }];
    setChat((old) => [...old, { role: "user", content }]);
    setQuestion("");
    run("chat", async (signal) => {
      const answer = await askAnalyst(result, history, { signal, provider });
      if (answer) setChat((old) => [...old, { role: "assistant", content: answer.answer, meta: answer }]);
    });
  };

  return (
    <div className="analyst">
      <div className="analyst-head">
        <label className="analyst-provider small">
          Answered by{" "}
          <select value={provider} onChange={(e) => pick(e.target.value)} disabled={Boolean(busy)}>
            {Object.entries(PROVIDERS).map(([value, p]) => <option key={value} value={value}>{p.label}</option>)}
          </select>
        </label>
        <StatusPill status={status} provider={provider} />
        <span className="small muted">
          {claude ? "Claude answers from this case's facts only (no lookups)." : "Runs on this computer. Only this case's facts are used."}
          {" "}Each answer is checked for addresses, transaction IDs and amounts that are not in the data.
        </span>
      </div>

      {claude && (
        <p className="analyst-warn small">
          Claude CLI: this case's facts and your questions are sent to Anthropic to be answered. Pick the local model
          to keep everything on this computer.
        </p>
      )}
      {status && !ready && <p className="analyst-help small">{detail}</p>}

      <section className="analyst-block">
        <div className="analyst-row">
          <button type="button" className="primary" onClick={explain} disabled={!ready || Boolean(busy)}>
            {busy === "explain" ? "Thinking…" : explanation ? "Explain again" : "Explain this case"}
          </button>
          {busy && (
            <button type="button" className="secondary compact" onClick={() => controller.current?.abort()}>Stop</button>
          )}
          {busy === "explain" && <span className="small muted">{PROVIDERS[provider].wait}</span>}
        </div>
        {explanation && <Answer answer={explanation} />}
        {explanation?.graphNodes != null && explanation.graphNodes !== (result.graph?.nodes?.length ?? 0) && (
          <p className="analyst-help small">
            The graph has changed since this explanation (from {explanation.graphNodes} to {result.graph?.nodes?.length ?? 0} nodes,
            for example after expanding the graph). Click "Explain again" to include the new nodes. New questions already use them.
          </p>
        )}
      </section>

      <section className="analyst-block">
        <h3 className="analyst-title">Ask the analyst</h3>
        {chat.length === 0 && (
          <div className="analyst-suggestions">
            {SUGGESTIONS.map((s) => (
              <button key={s} type="button" className="secondary compact" disabled={!ready || Boolean(busy)} onClick={() => ask(s)}>
                {s}
              </button>
            ))}
          </div>
        )}
        <div className="analyst-chat">
          {chat.map((m, i) => (
            <div key={i} className={`analyst-msg analyst-${m.role}`}>
              {m.role === "assistant" ? <Answer answer={m.meta} /> : <p>{m.content}</p>}
            </div>
          ))}
          {busy === "chat" && <div className="analyst-msg analyst-assistant muted small">{claude ? "Thinking…" : "Thinking… (it may look things up first)"}</div>}
          <div ref={chatEnd} />
        </div>
        <form className="analyst-ask" onSubmit={(e) => { e.preventDefault(); ask(); }}>
          <textarea
            rows={2}
            value={question}
            maxLength={2000}
            placeholder={ready ? "Ask about this case, e.g. 'Is 1Boat… on any list?'" : "The analyst is not ready yet"}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); }
            }}
            disabled={!ready}
          />
          <button type="submit" className="primary" disabled={!ready || Boolean(busy) || !question.trim()}>Ask</button>
        </form>
      </section>

      {error && <p className="analyst-error small">{error}</p>}
    </div>
  );
}

function StatusPill({ status, provider }) {
  if (!status) return <span className="pill pill-checking">Checking the analyst…</span>;
  if (provider === "claude_cli") {
    const c = status.claude_cli ?? {};
    if (c.ready) return <span className="pill pill-up">Claude CLI ready: {c.model}</span>;
    if (c.installed) return <span className="pill pill-warn">Claude CLI not signed in</span>;
    return <span className="pill pill-down">Claude CLI not installed</span>;
  }
  if (status.model_ready) return <span className="pill pill-up">Local model ready: {status.model}</span>;
  if (status.ollama_running) return <span className="pill pill-warn">Model not downloaded: {status.model}</span>;
  return <span className="pill pill-down">Local model offline</span>;
}

function Answer({ answer }) {
  const unverified = answer.checks?.unverified ?? [];
  return (
    <div className="analyst-answer">
      <div className="analyst-text"><RichText text={answer.answer} /></div>
      {unverified.length > 0 && (
        <p className="analyst-warn small">
          Not found in the case data, so check before relying on it:{" "}
          {unverified.map((u) => <code key={u.value}>{u.value}</code>).reduce((a, b) => [a, ", ", b])}
        </p>
      )}
      <p className="small muted">
        {answer.provider === "claude_cli" ? "Claude CLI · " : ""}{answer.model} · {answer.seconds}s{answer.tokens_per_second ? ` · ${answer.tokens_per_second} tokens/s` : ""}
        {answer.tools_used?.length > 0 && ` · looked up: ${answer.tools_used.map((t) => t.name.replaceAll("_", " ")).join(", ")}`}
        {unverified.length === 0 && answer.checks?.checked > 0 && ` · ${answer.checks.checked} identifiers and amounts checked against the data`}
        {" "}· AI-written; verify before acting.
      </p>
    </div>
  );
}

// Local models answer in light Markdown: paragraphs, "-" or "1." lists, **bold**, `code` and the odd "#" heading.
// Render just those as React elements (no HTML from the model is ever inserted into the page).
const LIST_ITEM = /^\s*(?:[-*\u2022]|\d+[.)])\s+/;

function RichText({ text }) {
  const blocks = [];
  for (const raw of String(text ?? "").split("\n")) {
    const line = raw.replace(/^#{1,6}\s+/, "").trimEnd();
    if (!line.trim()) {
      blocks.push(null);
      continue;
    }
    const ordered = /^\s*\d+[.)]\s+/.test(line);
    const last = blocks[blocks.length - 1];
    if (LIST_ITEM.test(line)) {
      const item = line.replace(LIST_ITEM, "");
      if (last && last.list && last.ordered === ordered) last.items.push(item);
      else blocks.push({ list: true, ordered, items: [item] });
    } else {
      blocks.push({ list: false, text: line, heading: /^#/.test(raw.trim()) });
    }
  }
  return blocks.filter(Boolean).map((block, i) => {
    if (!block.list) return <p key={i}>{block.heading ? <strong>{inline(block.text)}</strong> : inline(block.text)}</p>;
    const List = block.ordered ? "ol" : "ul";
    return <List key={i}>{block.items.map((item, j) => <li key={j}>{inline(item)}</li>)}</List>;
  });
}

function inline(text) {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) return <code key={i}>{part.slice(1, -1)}</code>;
    return part;
  });
}
