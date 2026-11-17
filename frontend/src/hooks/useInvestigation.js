import { useEffect, useRef, useState } from "react";
import { MODES, compatibleMethods } from "../config/modes.js";
import { FIXTURE_CASES } from "../data/fixtures.js";
import { runFixtureInvestigation, validateInput } from "../services/runFixtureInvestigation.js";
import { startInvestigation, streamGraphExpand } from "../api/client.js";
import { EXPAND_NODE_CAP, EXPAND_PER_NODE, expandableAddress, mergeExpansion } from "../services/expandInvestigationGraph.js";
import { MODEL_METHODS, normalizeBitcoin, requestModelScore, scoreKey, withModelScore } from "../services/modelScore.js";
import { requestRedFlags, withRedFlags } from "../services/redFlags.js";

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
  // "Expand node": the node being expanded, the ones already done, and the last message about it.
  const [expandingId, setExpandingId] = useState("");
  const [expandedIds, setExpandedIds] = useState(() => new Set());
  const [expandNote, setExpandNote] = useState(null); // { kind: "info" | "warning" | "error", text }
  const expandRef = useRef(null);
  // Live model scores, kept for the whole visit: "family|address" -> { status: loading | done | error, data, error, startedAt }.
  // Every Bitcoin address lookup asks for one automatically; any other Bitcoin address in the graph can be scored on request.
  const [scores, setScores] = useState({});
  const scoresRef = useRef(scores);
  scoresRef.current = scores;
  const [modelFamily, setModelFamilyState] = useState("xgboost");
  // Authority red flags (rule packs), kept for the whole visit: address -> { status, data, error }.
  const [flags, setFlags] = useState({});
  const flagsRef = useRef(flags);
  flagsRef.current = flags;

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
    expandRef.current?.abort();
    setExpandingId("");
    setExpandedIds(new Set());
    setExpandNote(null);
    const controller = new AbortController();
    requestRef.current = controller;

    // Every Bitcoin address lookup gets the live model score, asked for at the same time as the lookup.
    const scoredHere = request.inputType === "bitcoin_address" && request.method !== "fixture_review"
      && !MODEL_METHODS.has(request.method);
    if (scoredHere) scoreAddress(normalizeBitcoin(request.value), modelFamily);
    // ...and the authority red flags, also at the same time (the authority_rules method brings them itself).
    const flaggedHere = request.inputType === "bitcoin_address" && request.method !== "fixture_review"
      && request.method !== "authority_rules";
    if (flaggedHere) checkFlags(normalizeBitcoin(request.value));

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
      // A model method (or an older server) already returns the score: remember it like a separate one.
      if (data.explanation?.benchmark === "live" && data.explanation.address) {
        const family = data.explanation.model?.family ?? "xgboost";
        setScores((old) => ({ ...old, [scoreKey(family, data.explanation.address)]: { status: "done", data: data.explanation } }));
        if (MODEL_METHODS.has(request.method)) setModelFamilyState(family);
      }
      // The authority_rules method returns the red flags inside the result: remember them like a separate check.
      const inside = data.explanation?.red_flags;
      if (inside && data.input?.type === "bitcoin_address") {
        const address = normalizeBitcoin(data.input.value);
        setFlags((old) => ({ ...old, [address]: { status: "done", data: { ...inside, address, inResult: true,
          disclaimer: data.limitations.find((l) => l.startsWith("Red flags are")) ?? "" } } }));
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

  // Fetch the counterparties of one address node and add them to the graph (one hop, streamed).
  async function expandNode(nodeId) {
    const target = expandableAddress(result, nodeId);
    if (!target || expandingId) return;
    if (result.graph.nodes.length >= EXPAND_NODE_CAP) {
      setExpandNote({ kind: "warning", text: `The graph already has ${EXPAND_NODE_CAP} nodes, the most this view draws smoothly. Use Advanced graph for bigger graphs.` });
      return;
    }
    const controller = new AbortController();
    expandRef.current = controller;
    const investigationId = result.investigation_id;
    const evidenceId = `ex${expandedIds.size + 1}`;
    const label = result.graph.nodes.find((n) => n.id === nodeId)?.label ?? target.address;
    const added = { nodes: new Set(), links: new Set() };
    const problems = [];
    let stopped = null;
    let working = result;
    setExpandingId(nodeId);
    setExpandNote({ kind: "info", text: `Expanding ${label}...` });
    try {
      await streamGraphExpand({
        chain: target.chain, addresses: [target.address], hops: 1,
        max_new_nodes: Math.max(1, EXPAND_NODE_CAP - result.graph.nodes.length), per_node_limit: EXPAND_PER_NODE,
      }, {
        signal: controller.signal,
        onEvent: (event) => {
          if (event.event === "batch") {
            // Merge into a local copy (the only writer while this runs), so the count below is exact.
            working = mergeExpansion(working, event, target, evidenceId, added);
            const next = working;
            setResult((old) => (old?.investigation_id === investigationId ? next : old));
          } else if (event.event === "error" || event.event === "warning") {
            problems.push(event.message);
          } else if (event.event === "done") {
            stopped = event.stopped;
          }
        },
      });
      if (controller.signal.aborted) return;
      setExpandedIds((old) => new Set(old).add(nodeId));
      const text = problems.length ? problems.join(" ")
        : added.nodes.size || added.links.size
          ? `Added ${added.nodes.size} node(s) and ${added.links.size} link(s) around ${label}.`
            + (stopped === "node_limit" ? " Stopped at the node limit." : "")
          : `Nothing new: every counterparty of ${label} is already in the graph.`;
      setExpandNote({ kind: problems.length ? "warning" : "info", text });
    } catch (err) {
      if (!controller.signal.aborted) setExpandNote({ kind: "error", text: err.message });
    } finally {
      if (expandRef.current === controller) {
        expandRef.current = null;
        setExpandingId("");
      }
    }
  }

  // Ask for the live score of one Bitcoin address (once per model, unless force).
  async function scoreAddress(address, family = modelFamily, { force = false } = {}) {
    if (!address) return;
    const key = scoreKey(family, address);
    const known = scoresRef.current[key];
    if (!force && known && known.status !== "error") return;
    const loading = { status: "loading", startedAt: Date.now() };
    scoresRef.current = { ...scoresRef.current, [key]: loading };
    setScores((old) => ({ ...old, [key]: loading }));
    try {
      const data = await requestModelScore(address, family);
      // Only replace our own "loading" entry (the lookup itself may have brought the score meanwhile).
      setScores((old) => (old[key] === loading ? { ...old, [key]: { status: "done", data } } : old));
    } catch (err) {
      setScores((old) => (old[key] === loading ? { ...old, [key]: { status: "error", error: err.message } } : old));
    }
  }

  // Ask for the authority red flags of one Bitcoin address (once, unless force).
  async function checkFlags(address, { force = false } = {}) {
    if (!address) return;
    const known = flagsRef.current[address];
    if (!force && known && known.status !== "error") return;
    const loading = { status: "loading", startedAt: Date.now() };
    flagsRef.current = { ...flagsRef.current, [address]: loading };
    setFlags((old) => ({ ...old, [address]: loading }));
    try {
      const data = await requestRedFlags(address);
      setFlags((old) => (old[address] === loading ? { ...old, [address]: { status: "done", data } } : old));
    } catch (err) {
      setFlags((old) => (old[address] === loading ? { ...old, [address]: { status: "error", error: err.message } } : old));
    }
  }

  const flagsOf = (address) => (address ? flags[address] ?? null : null);

  // When the looked-up address's red flags are in, add their findings and evidence to the result itself.
  useEffect(() => {
    const subject = subjectAddress(result);
    if (!subject) return;
    const entry = flags[subject];
    if (entry?.status !== "done" || entry.data.inResult || result.red_flags === entry.data) return;
    const id = result.investigation_id;
    setResult((old) => (old?.investigation_id === id ? withRedFlags(old, entry.data) : old));
  }, [result, flags]);

  // Switch model (XGBoost / Random Forest): the looked-up address is scored with it too.
  function setModelFamily(family) {
    setModelFamilyState(family);
    const subject = subjectAddress(result);
    if (subject) scoreAddress(subject, family);
  }

  const scoreOf = (address, family = modelFamily) => (address ? scores[scoreKey(family, address)] ?? null : null);

  // When the looked-up address's score (for the chosen model) is in, show it in the result itself:
  // the Findings, Why Flagged? and Evidence tabs then include it, in every design.
  useEffect(() => {
    const subject = subjectAddress(result);
    if (!subject) return;
    const entry = scores[scoreKey(modelFamily, subject)];
    if (entry?.status !== "done" || result.explanation === entry.data) return;
    const id = result.investigation_id;
    setResult((old) => (old?.investigation_id === id ? withModelScore(old, entry.data) : old));
  }, [result, scores, modelFamily]);

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
    expandingId, expandedIds, expandNote, canExpand: (id) => Boolean(expandableAddress(result, id)), expandNode,
    modelFamily, setModelFamily, scoreAddress, scoreOf, subjectAddress: subjectAddress(result),
    checkFlags, flagsOf,
    setSelectedId, setActiveTab,
    handleFormChange, handleCaseChange, handleStart, handleCancel, handleEvidenceClick, investigateLive,
  };
}

// The Bitcoin address an investigation was about, or null (transactions, other chains, demo cases).
function subjectAddress(result) {
  if (!result || result.data_mode !== "live" || result.input?.type !== "bitcoin_address") return null;
  return normalizeBitcoin(result.input.value);
}
