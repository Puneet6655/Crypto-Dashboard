import { useState } from "react";
import { useAuth } from "../auth/AuthContext.jsx";
import SignupExtras, { EMPTY_EXTRAS, extrasBody, extrasProblem } from "./SignupExtras.jsx";
import { LoginLegalLinks } from "./LegalBits.jsx";
import "./legal.css";

// Shown instead of the dashboard when a signed-in account still owes something: accounts made before these
// questions existed (age, user type) and everyone after the terms change (accept again). Works in both designs.
export default function CompleteProfile() {
  const { user, config, completeProfile, signOut } = useAuth();
  const needs = user.needs ?? [];
  const askAge = needs.includes("age");
  const askType = needs.includes("user_type") || needs.includes("age");
  const [value, setValue] = useState({ ...EMPTY_EXTRAS, userType: user.user_type ?? "", organisation: user.organisation ?? "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const termsOnly = !askAge && !askType;

  async function submit(event) {
    event.preventDefault();
    const problem = extrasProblem(value, config?.min_age, { askAge, askType });
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError("");
    try {
      await completeProfile({ ...extrasBody(value), user_type: value.userType || user.user_type });
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <div className="legal-standalone">
      <form className="card form-grid complete-profile" onSubmit={submit}>
        <h1>{termsOnly ? "Our terms have changed" : "One more step"}</h1>
        <p className="muted small">
          {termsOnly
            ? "Please read the updated terms of use and privacy policy and accept them to keep using the dashboard."
            : "Before you continue, please confirm your age, tell us what describes you best, and accept the terms."}
        </p>
        <SignupExtras value={value} onChange={setValue} config={config} askAge={askAge} askType={askType} />
        {error && <p className="notice error small" role="alert">{error}</p>}
        <button type="submit" className="primary" disabled={busy}>{busy ? "Saving..." : "Continue"}</button>
        <button type="button" className="secondary" onClick={signOut}>Sign out</button>
        <LoginLegalLinks />
      </form>
    </div>
  );
}
