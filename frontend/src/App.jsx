import { useState } from "react";
import { MODES, compatibleMethods } from "./config/modes.js";
import { FIXTURE_CASES } from "./data/fixtures.js";
import { runFixtureInvestigation, validateInput } from "./services/runFixtureInvestigation.js";
import ControlsPanel from "./components/ControlsPanel.jsx";
import GraphView from "./components/GraphView.jsx";
import EntityDetails from "./components/EntityDetails.jsx";
import ResultTabs from "./components/ResultTabs.jsx";

const FIRST_MODE = MODES[0];
const firstReadyMethod = (mode, inputType) =>
  compatibleMethods(mode, inputType).find((m) => m.status === "ready")?.id ?? "";

export default function App() {
  // What the analyst has chosen in the controls.
  const [form, setForm] = useState({
    mode: FIRST_MODE.id,
    inputType: FIRST_MODE.inputTypes[0],
    value: "",
    caseId: "",
    method: firstReadyMethod(FIRST_MODE.id, FIRST_MODE.inputTypes[0]),
  });
  // What happened when they pressed Start.
  const [status, setStatus] = useState("idle"); // idle | loading | done | empty | error
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [highlightedEvidence, setHighlightedEvidence] = useState("");

  // Merge a change into the form and keep dependent choices valid.
  function handleFormChange(changes) {
    setForm((old) => {
      const next = { ...old, ...changes };
      if (changes.mode) {
        const mode = MODES.find((m) => m.id === changes.mode);
        next.inputType = mode.inputTypes[0];
        next.value = "";
        next.caseId = "";
      }
      if (changes.mode || changes.inputType) {
        next.method = firstReadyMethod(next.mode, next.inputType);
      }
      return next;
    });
  }

  // Picking a preloaded case fills in its input type and value.
  function handleCaseChange(caseId) {
    const chosen = FIXTURE_CASES.find((c) => c.caseId === caseId);
    if (!chosen) {
      setForm((old) => ({ ...old, caseId: "" }));
      return;
    }
    setForm((old) => ({
      ...old,
      caseId,
      inputType: chosen.input.type,
      value: chosen.input.value,
      method: firstReadyMethod(old.mode, chosen.input.type),
    }));
  }

  async function handleStart() {
    const problem = validateInput(form.inputType, form.value);
    if (problem) {
      setStatus("error");
      setError(problem);
      setResult(null);
      return;
    }
    setStatus("loading");
    setResult(null);
    setSelectedId("");
    try {
      const data = await runFixtureInvestigation(form);
      if (data === null) {
        setStatus("empty");
        return;
      }
      setResult(data);
      setSelectedId(data.graph.nodes[0]?.id ?? "");
      setStatus("done");
    } catch (err) {
      setError(err.message);
      setStatus("error");
    }
  }

  return (
    <div className="app">
      <header className="card header">
        <h1>Payments Intelligence Dashboard</h1>
        <span className={`data-badge data-${result?.data_mode ?? "none"}`}>
          Data: {result ? result.data_mode.toUpperCase() : "none yet"}
        </span>
      </header>

      <main className="layout">
        <ControlsPanel
          form={form}
          onFormChange={handleFormChange}
          onCaseChange={handleCaseChange}
          onStart={handleStart}
          isLoading={status === "loading"}
        />
        <GraphView status={status} error={error} result={result} selectedId={selectedId} onSelect={setSelectedId} />
        <EntityDetails result={result} selectedId={selectedId} />
      </main>

      <ResultTabs result={result} highlightedEvidence={highlightedEvidence} onEvidenceClick={setHighlightedEvidence} />
    </div>
  );
}
