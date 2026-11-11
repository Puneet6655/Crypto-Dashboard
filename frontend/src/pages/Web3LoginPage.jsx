import { useState } from "react";
import { useAuth } from "../auth/AuthContext.jsx";
import Icon from "../components/Icon.jsx";
import ParticleSwirl from "../components/ParticleSwirl.jsx";
import useCountUp from "../hooks/useCountUp.js";
import "../styles/web3-login.css";

// Coin badges that float around the particle swirl.
const COINS = [
  { name: "bitcoin", glyph: "\u20bf" },
  { name: "ethereum", glyph: <svg viewBox="0 0 24 24" width="20" height="20"><path d="M12 2 5 12.2 12 16l7-3.8L12 2Zm0 15.4-7-3.8L12 22l7-8.4-7 3.8Z" fill="currentColor" /></svg> },
  { name: "solana", glyph: <svg viewBox="0 0 24 24" width="20" height="20"><path d="M6 5h14l-2 3H4l2-3Zm-2 5.5h14l2 3H6l-2-3ZM6 16h14l-2 3H4l2-3Z" fill="currentColor" /></svg> },
  { name: "tether", glyph: "\u20ae" },
];

// Thin falling light lines in the background: [left %, delay s, duration s].
const RAIN = [[8, 0, 6], [18, 2.5, 8], [31, 1, 7], [46, 4, 9], [63, 0.5, 7.5], [74, 3, 6.5], [88, 1.8, 8.5], [95, 5, 7]]
  .map(([left, delay, dur]) => ({ left: `${left}%`, animationDelay: `${delay}s`, animationDuration: `${dur}s` }));

function Stat({ value, label, decimals = 0, delay = 0 }) {
  const shown = useCountUp(value, { decimals, delay });
  return (
    <div className="w3-login-stat">
      <strong>{shown}</strong>
      <span>{label}</span>
    </div>
  );
}

// Web3-style version of the sign-in page (purple glass, light beam, particle swirl).
// Kept separate from LoginPage.jsx: App.jsx only shows it when VITE_LOGIN_DESIGN=web3 or the URL has ?web3.
// Sign in, or create a viewer account. Admin rights are never given from this page.
export default function Web3LoginPage() {
  const { config, signIn, signUp } = useAuth();
  const [mode, setMode] = useState("signin");
  const [form, setForm] = useState({ email: "", name: "", password: "", confirm: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const signupOpen = config?.signup_enabled;
  const minLength = config?.min_password_length ?? 10;
  const set = (key) => (event) => setForm((old) => ({ ...old, [key]: event.target.value }));

  async function submit(event) {
    event.preventDefault();
    setError("");
    if (mode === "signup" && form.password !== form.confirm) {
      setError("The two passwords are different.");
      return;
    }
    setBusy(true);
    try {
      if (mode === "signin") await signIn(form.email, form.password);
      else await signUp(form.email, form.name, form.password);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <div className="w3-login">
      <section className="w3-login-art" aria-hidden="true">
        <div className="w3-login-beam" />
        <div className="w3-login-rain">{RAIN.map((r, i) => <span key={i} style={r} />)}</div>
        <div className="w3-login-art-inner">
          <span className="w3-login-pill"><Icon name="globe" size={14} /> Multi-chain payments intelligence</span>
          <div className="w3-login-orb">
            <ParticleSwirl className="w3-login-swirl" />
            {COINS.map((coin) => (
              <span key={coin.name} className={`w3-coin w3-coin-${coin.name}`} title={coin.name}>{coin.glyph}</span>
            ))}
          </div>
          <h2>Payments Intelligence</h2>
          <p>Follow Bitcoin payments, screen them against sanctions and ransomware lists, and build cases with evidence you can trace back to its source.</p>
          <div className="w3-login-stats">
            <Stat value={10779} label="Labelled exchange addresses" delay={200} />
            <Stat value={3} label="Chains watched live" delay={350} />
            <Stat value={0.76} decimals={2} label="Fraud model PR-AUC" delay={500} />
          </div>
        </div>
      </section>

      <section className="w3-login-panel">
        <form className="w3-login-card" onSubmit={submit}>
          <div className="segmented" role="tablist" aria-label="Sign in or create an account">
            <button type="button" role="tab" aria-selected={mode === "signin"} className={mode === "signin" ? "active" : ""}
              onClick={() => { setMode("signin"); setError(""); }}>Sign in</button>
            <button type="button" role="tab" aria-selected={mode === "signup"} className={mode === "signup" ? "active" : ""}
              onClick={() => { setMode("signup"); setError(""); }} disabled={!signupOpen}
              title={signupOpen ? "" : "Sign-up is closed"}>Create account</button>
          </div>

          <h1>{mode === "signin" ? "Welcome back" : "Create your account"}</h1>
          <p className="muted small">
            {mode === "signin"
              ? "Sign in to open the dashboard."
              : "New accounts can view everything on the dashboard. An admin can give you more access later."}
          </p>

          {!config?.database && (
            <p className="notice warn small">
              The API has no database connected, so nobody can sign in yet. Set DATABASE_URL in backend/.env and restart it.
            </p>
          )}

          {mode === "signup" && (
            <label className="field">
              <span className="field-label">Your name</span>
              <input type="text" value={form.name} onChange={set("name")} autoComplete="name" required maxLength={80} />
            </label>
          )}
          <label className="field">
            <span className="field-label">Email</span>
            <input type="email" value={form.email} onChange={set("email")} autoComplete="email" required maxLength={254} />
          </label>
          <label className="field">
            <span className="field-label">Password</span>
            <input
              type="password"
              value={form.password}
              onChange={set("password")}
              autoComplete={mode === "signin" ? "current-password" : "new-password"}
              required
              minLength={mode === "signup" ? minLength : undefined}
              maxLength={256}
            />
            {mode === "signup" && <span className="small muted">At least {minLength} characters. A short phrase works well.</span>}
          </label>
          {mode === "signup" && (
            <label className="field">
              <span className="field-label">Password again</span>
              <input type="password" value={form.confirm} onChange={set("confirm")} autoComplete="new-password" required />
            </label>
          )}

          {error && <p className="notice error small" role="alert">{error}</p>}

          <button type="submit" className="primary" disabled={busy || !config?.database}>
            {busy ? "Please wait..." : mode === "signin" ? "Sign in" : "Create account"}
          </button>
          <p className="small muted center">
            <Icon name="lock" size={12} /> Passwords are stored only as salted scrypt hashes.
          </p>
        </form>
      </section>
    </div>
  );
}
