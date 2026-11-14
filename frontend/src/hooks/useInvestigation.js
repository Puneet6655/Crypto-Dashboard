import { useRef, useState } from "react";
import { MODES, compatibleMethods } from "../config/modes.js";
import { FIXTURE_CASES } from "../data/fixtures.js";
import { runFixtureInvestigation, validateInput } from "../services/runFixtureInvestigation.js";
import { startInvestigation } from "../api/client.js";

const FIRST_MODE = MODES[0];
const firstReadyMethod = (mode, inputType) =>
  compatibleMethods(mode, inputType).find((m) => m.status === "ready")?.id ?? "";

// Everything about the current investigation: the form, the request in flight and its result.
// It lives in App (not in the Investigate page) so switching pages keeps the result on screen.
export function useInvestigation() {
  // What the analyst has chosen in the controls.
  const [form, setForm] = useState({
    mode: FIRST_MODE.id,
    inputType: FIRST_MODE.inputTypes[0],
    value: "",
    caseId: "",
    method: firstReadyMethod(FIRST_MODE.id, FIRST_MODE.inputTypes[0]),
  });
  // What happened when they pressed Start.
  const [status, setStatus] = useState("idle"); // idle | loading | done | empty | error | cancelled
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [highlightedEvidence, setHighlightedEvidence] = useState("");
  const [activeTab, setActiveTab] = useState("Findings");
  const [startedAt, setStartedAt] = useState(0);
  // The AbortController of the request in flight. A ref, not state, because
  // changing it should not redraw the screen.
  const requestRef = useRef(null);

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
      // Typing your own value leaves the demo case, so switch away from the demo method.
      if (changes.value !== undefined && old.caseId) {
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
      method: "fixture_review", // demo cases always use their labelled synthetic data
    }));
  }

  async function run(request) {
    const problem = validateInput(request.inputType, request.value);
    if (problem) {
      setStatus("error");
      setError(problem);
      setResult(null);
      return;
    }
    requestRef.current?.abort(); // a new Start replaces any request still running
    const controller = new AbortController();
    requestRef.current = controller;

    setStatus("loading");
    setStartedAt(Date.now());
    setResult(null);
    setSelectedId("");
    setHighlightedEvidence("");
    setActiveTab("Findings");
    try {
      const data =
        request.method === "fixture_review"
          ? await runFixtureInvestigation(request)
          : await startInvestigation(request, { signal: controller.signal });
      // If the analyst pressed Cancel (or Start again) while we waited, drop this answer.
      if (controller.signal.aborted) return;
      if (data === null) {
        setStatus("empty");
        return;
      }
      setResult(data);
      setSelectedId(data.graph.nodes[0]?.id ?? "");
      setStatus("done");
    } catch (err) {
      if (controller.signal.aborted) return;
      setError(err.message);
      setStatus("error");
    }
  }

  const handleStart = () => run(form);

  // Look up a Bitcoin address or transaction live, e.g. from a case ("Investigate" button).
  function investigateLive(inputType, value, mode = "crypto_investigation", method = "live_lookup") {
    const request = { mode, inputType, value, caseId: "", method };
    setForm(request);
    run(request);
  }

  function handleCancel() {
    requestRef.current?.abort();
    requestRef.current = null;
    setStatus("cancelled");
  }

  // Clicking an evidence ID anywhere opens the Evidence tab on that item.
  function handleEvidenceClick(evidenceId) {
    setHighlightedEvidence(evidenceId);
    setActiveTab("Evidence");
  }

  return {
    form, status, result, error, selectedId, highlightedEvidence, activeTab, startedAt,
    setSelectedId, setActiveTab,
    handleFormChange, handleCaseChange, handleStart, handleCancel, handleEvidenceClick, investigateLive,
  };
}
