import { useState } from "react";
import { apiFetch } from "../api/client.js";
import { ROLE_LABELS, useAuth } from "../auth/AuthContext.jsx";
import PageHeader from "../components/PageHeader.jsx";
import Icon from "../components/Icon.jsx";
import { useToast } from "../components/Toasts.jsx";
import YourData from "../legal/YourData.jsx";

const ROLE_HELP = {
  viewer: "You can use every page of the dashboard. Ask an admin if you need to build cases.",
  analyst: "You can also create cases and add addresses and transactions to them.",
  admin: "You can also edit the sources list, use BigQuery tools, delete cases and manage users.",
};

export default function AccountPage() {
  const { user, signOut } = useAuth();
  const toast = useToast();
  const [form, setForm] = useState({ current: "", next: "", confirm: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (key) => (event) => setForm((old) => ({ ...old, [key]: event.target.value }));

  async function changePassword(event) {
    event.preventDefault();
    setError("");
    if (form.next !== form.confirm) {
      setError("The two new passwords are different.");
      return;
    }
    setBusy(true);
    try {
      await apiFetch("/api/auth/password", {
        method: "POST",
        body: { current_password: form.current, new_password: form.next },
      });
      toast("Password changed. Please sign in with the new one.");
      await signOut();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader icon="account" title="Your account" subtitle={user.email} />
      <div className="grid-2">
        <section className="card">
          <h2>Access</h2>
          <p><span className={`role-badge role-${user.role}`}>{ROLE_LABELS[user.role]}</span></p>
          <p className="muted">{ROLE_HELP[user.role]}</p>
          <button type="button" className="secondary" onClick={signOut}><Icon name="logout" size={16} /> Sign out</button>
        </section>
        {user.id !== null && (
          <form className="card form-grid" onSubmit={changePassword}>
            <h2>Change password</h2>
            <label className="field">
              <span className="field-label">Current password</span>
              <input type="password" value={form.current} onChange={set("current")} autoComplete="current-password" required />
            </label>
            <label className="field">
              <span className="field-label">New password</span>
              <input type="password" value={form.next} onChange={set("next")} autoComplete="new-password" required minLength={10} />
            </label>
            <label className="field">
              <span className="field-label">New password again</span>
              <input type="password" value={form.confirm} onChange={set("confirm")} autoComplete="new-password" required />
            </label>
            {error && <p className="notice error small" role="alert">{error}</p>}
            <button type="submit" className="primary" disabled={busy}>{busy ? "Saving..." : "Change password"}</button>
            <p className="small muted">Changing it signs you out on every device.</p>
          </form>
        )}
      </div>
      <YourData />
    </>
  );
}
