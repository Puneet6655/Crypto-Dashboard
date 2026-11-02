import { useState } from "react";
import { apiFetch } from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import PageHeader, { formatDate } from "../components/PageHeader.jsx";
import ShapChart from "../components/ShapChart.jsx";

const FAMILY = { logistic_regression: "Logistic regression (baseline)", random_forest: "Random Forest", xgboost: "XGBoost" };
const pct = (v) => (v === null || v === undefined ? "n/a" : `${(v * 100).toFixed(2)}%`);
const n3 = (v) => (v === null || v === undefined ? "n/a" : v.toFixed(3));

// Model registry, the comparison panel (Milestone 12) and scoring of dataset addresses with SHAP.
export default function ModelsPage() {
  const registry = useApi("/api/models");
  const data = registry.data;

  return (
    <>
      <PageHeader icon="shield" title="Models" subtitle="Random Forest vs XGBoost vs a logistic-regression baseline, on one fixed offline benchmark." />

      <div className="notice warn">
        <strong>Offline benchmark only.</strong> These models were trained on Elliptic++ wallets from 2016-2017.
        Scores and SHAP explanations work only for addresses in the benchmark dataset, because live lookups cannot
        build the hop features yet. A score is a pattern match, not proof of a crime.
      </div>

      {registry.loading && !data && <p className="muted">Loading the model registry...</p>}
      {registry.error && <p className="error-text">{registry.error}</p>}
      {data && !data.available && (
        <section className="card">
          <h2>No trained models on this server</h2>
          <p className="muted">{data.detail}</p>
        </section>
      )}
      {data?.available && (
        <>
          <Comparison data={data} />
          <div className="grid-2">
            <ScorePanel models={data.models} />
            <AlertThreshold alert={data.alert_threshold} />
          </div>
          <Registry data={data} />
        </>
      )}
    </>
  );
}

function Comparison({ data }) {
  const rows = [...data.models].sort((a, b) => a.feature_set.localeCompare(b.feature_set) || b.metrics["PR-AUC"] - a.metrics["PR-AUC"]);
  const best = Math.max(...rows.map((m) => m.metrics["PR-AUC"]));
  return (
    <section className="card">
      <h2>Comparison</h2>
      <p className="small muted">
        {data.benchmark}. Test rows: {data.test_rows.toLocaleString()} ({data.test_illicit.toLocaleString()} illicit).
        Each threshold was chosen on steps 31-34 before the test. "Beats baseline" means the mean PR-AUC over three
        seeds is above the logistic regression on the same features by more than twice the seed spread.
      </p>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Model</th><th>Features</th><th className="num">PR-AUC</th><th className="num">ROC-AUC</th>
              <th className="num">Precision</th><th className="num">Recall</th><th className="num">F1</th>
              <th className="num">PR-AUC over seeds</th><th>Beats baseline</th><th className="num">TP / FP / FN</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.id} className={m.metrics["PR-AUC"] === best ? "row-best" : ""}>
                <td>{FAMILY[m.family] ?? m.family}</td>
                <td>
                  {m.feature_set}{" "}
                  <span className={`pill ${m.live_compatible ? "pill-up" : "pill-muted"}`}>
                    {m.live_compatible ? "live-compatible" : "dataset only"}
                  </span>
                </td>
                <td className="num"><Bar value={m.metrics["PR-AUC"]} /> {n3(m.metrics["PR-AUC"])}</td>
                <td className="num">{n3(m.metrics["ROC-AUC"])}</td>
                <td className="num">{n3(m.metrics.precision)}</td>
                <td className="num">{n3(m.metrics.recall)}</td>
                <td className="num">{n3(m.metrics.F1)}</td>
                <td className="num">{n3(m.seeds.pr_auc_mean)} ± {n3(m.seeds.pr_auc_std)} ({m.seeds.n})</td>
                <td>{m.beats_baseline === null ? <span className="muted">baseline</span> : m.beats_baseline ? "yes" : "no"}</td>
                <td className="num small">{m.confusion.tp.toLocaleString()} / {m.confusion.fp.toLocaleString()} / {m.confusion.fn.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Bar({ value }) {
  return <span className="metric-bar" aria-hidden="true"><span style={{ width: `${Math.round(value * 100)}%` }} /></span>;
}

function ScorePanel({ models }) {
  const examples = useApi("/api/models/examples");
  const [address, setAddress] = useState("");
  const [modelId, setModelId] = useState(models.find((m) => m.family === "xgboost" && !m.live_compatible)?.id ?? models[0]?.id);
  const [state, setState] = useState({ status: "idle" });

  async function run(value = address, id = modelId) {
    if (!value.trim()) return;
    setState({ status: "loading" });
    try {
      const body = await apiFetch("/api/models/score", { method: "POST", body: { address: value.trim(), model_id: id }, timeoutMs: 120000 });
      setState({ status: "done", body });
    } catch (err) {
      setState({ status: "error", error: err.message });
    }
  }

  const result = state.body;
  return (
    <section className="card">
      <h2>Score a dataset address</h2>
      <form className="model-form" onSubmit={(event) => { event.preventDefault(); run(); }}>
        <label>
          Bitcoin address from the benchmark dataset
          <input value={address} onChange={(event) => setAddress(event.target.value)} placeholder="1... or 3... or bc1..." />
        </label>
        <label>
          Model
          <select value={modelId} onChange={(event) => setModelId(event.target.value)}>
            {models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        </label>
        <button type="submit" className="primary" disabled={state.status === "loading"}>
          {state.status === "loading" ? "Scoring..." : "Score and explain"}
        </button>
      </form>
      {examples.data?.length > 0 && (
        <p className="small">
          Try:{" "}
          {examples.data.map((e) => (
            <button key={`${e.address}-${e.step}`} type="button" className="link" title={e.address}
              onClick={() => { setAddress(e.address); run(e.address); }}>
              {e.kind}, step {e.step}
            </button>
          )).reduce((all, el, i) => (i ? [...all, " · ", el] : [el]), [])}
        </p>
      )}
      {state.status === "error" && <p className="error-text">{state.error}</p>}
      {result && state.status === "done" && (
        <div className="score-result">
          <div className={`score-badge ${result.flagged ? "flagged" : "clear"}`}>
            <strong>{result.score.toFixed(3)}</strong>
            <span>{result.flagged ? "at or above" : "below"} threshold {result.threshold.toFixed(2)}</span>
          </div>
          <p className="small">
            {result.model.name}, version {result.model.version}. Dataset row at step {result.step}
            {result.steps_available.length > 1 ? ` (rows at steps ${result.steps_available.join(", ")})` : ""}; {result.label}.
          </p>
          <h3>Why this score? (SHAP)</h3>
          <ShapChart explanation={result.explanation} />
        </div>
      )}
    </section>
  );
}

function AlertThreshold({ alert }) {
  if (!alert?.model_id) return null;
  const bands = ["1-49", "50-75", "76-101"];
  return (
    <section className="card">
      <h2>Alert threshold on recent data</h2>
      <p className="small muted">
        XGBoost with hop features, trained on every label of steps 1-49. With no labels after step 49, the strict alert
        threshold is the score that flags 1% of random active addresses. Picked on steps 50-75, checked on 76-101.
      </p>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>Threshold</th><th className="num">Value</th>{bands.map((b) => <th key={b} className="num">Random flagged, steps {b}</th>)}<th className="num">Ransomware caught, steps 50+</th></tr>
          </thead>
          <tbody>
            {[["Old (steps 1-49)", "old_threshold"], ["Recent (steps 50-75)", "recent_threshold"]].map(([label, key]) => (
              <tr key={key}>
                <td>{label}</td>
                <td className="num">{alert[key].toFixed(4)}</td>
                {bands.map((b) => <td key={b} className="num">{pct(alert.random_flagged[key][b])}</td>)}
                <td className="num">{pct(alert.ransomware_flagged[key])}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="small muted">Ransomware rows after step 49: {alert.ransomware_rows_steps_50_101.toLocaleString()}.</p>
    </section>
  );
}

function Registry({ data }) {
  return (
    <section className="card">
      <h2>Registry</h2>
      <p className="small muted">Built {formatDate(data.generated_utc)} by training/model_registry.py. {data.scoring_rows?.toLocaleString()} dataset rows can be scored.</p>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>ID</th><th>Version</th><th className="num">Features</th><th className="num">Threshold</th><th className="num">Training rows</th><th>Threshold rule</th></tr></thead>
          <tbody>
            {data.models.map((m) => (
              <tr key={m.id}>
                <td className="mono small">{m.id}</td>
                <td className="mono small">{m.version}</td>
                <td className="num">{m.feature_count}</td>
                <td className="num">{m.threshold.toFixed(2)}</td>
                <td className="num">{m.trained_rows.toLocaleString()}</td>
                <td className="small">{m.threshold_rule}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
