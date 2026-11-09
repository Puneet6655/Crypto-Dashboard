import { useState } from "react";
import { useAuth } from "../auth/AuthContext.jsx";
import Icon from "../components/Icon.jsx";

// Sign in, or create a viewer account. Admin rights are never given from this page.
export default function LoginPage() {
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
    <div className="login">
      <section className="login-art" aria-hidden="true">
        <div className="login-art-inner">
          <span className="brand-mark large"><Icon name="brand" size={34} /></span>
          <h2>Payments Intelligence</h2>
          <p>Follow Bitcoin payments, screen them against sanctions and ransomware lists, and build cases with evidence you can trace back to its source.</p>
          <ul className="login-points">
            <li><Icon name="investigate" size={18} /> Live lookups with automatic backup sources</li>
            <li><Icon name="shield" size={18} /> OFAC and Ransomwhere screening</li>
            <li><Icon name="cases" size={18} /> Cases built from anything you click</li>
          </ul>
        </div>
      </section>

      <section className="login-panel">
        <form className="login-card" onSubmit={submit}>
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
