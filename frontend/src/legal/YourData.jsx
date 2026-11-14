import { useState } from "react";
import { API_BASE_URL, getToken } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import Icon from "../components/Icon.jsx";
import { useToast } from "../components/Toasts.jsx";
import "./legal.css";

const LABELS = {
  researcher: "Researcher",
  student: "Student",
  organisation: "Organisation or company",
  law_enforcement: "Law enforcement or government",
  other: "Other",
};

// Account page: your details, download all your data, and delete your account. Uses shared classes only,
// so it works in the classic and Web3 designs.
export default function YourData() {
  const { user, config, deleteAccount } = useAuth();
  const toast = useToast();
  const [downloading, setDownloading] = useState(false);
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [word, setWord] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  if (user.id === null) return null;
  const types = config?.user_types ?? LABELS;

  async function download() {
    setDownloading(true);
    try {
      const response = await fetch(`${API_BASE_URL}/api/auth/export`, { headers: { Authorization: `Bearer ${getToken()}` } });
      if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? `HTTP ${response.status}`);
      const blob = await response.blob();
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `my-data-${new Date().toISOString().slice(0, 10)}.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    } catch (err) {
      toast(`Could not download your data: ${err.message}`, { tone: "error" });
    }
    setDownloading(false);
  }

  async function remove(event) {
    event.preventDefault();
    setError("");
    if (word.trim().toUpperCase() !== "DELETE") {
      setError("Type DELETE to confirm.");
      return;
    }
    if (!window.confirm("Delete your account and all your data now? This cannot be undone.")) return;
    setBusy(true);
    try {
      await deleteAccount(password, word);
      toast("Your account and data have been deleted.");
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <div className="grid-2 your-data">
      <section className="card">
        <h2>Your details and data</h2>
        <dl className="legal-facts">
          <dt>Name</dt><dd>{user.display_name}</dd>
          <dt>Describes you</dt><dd>{types[user.user_type] ?? "Not given"}{user.organisation ? `, ${user.organisation}` : ""}</dd>
          <dt>Terms accepted</dt>
          <dd>{user.terms_accepted_at ? new Date(user.terms_accepted_at).toLocaleDateString() : "Not yet"} (version {user.terms_version ?? "none"})</dd>
        </dl>
        <p className="muted small">
          Download a copy of everything stored about your account: profile, sessions, cases, items you added, watchlist
          entries and audit log. To correct something else, contact us (see the <a href="#/privacy">privacy policy</a>).
        </p>
        <button type="button" className="secondary" onClick={download} disabled={downloading}>
          <Icon name="download" size={16} /> {downloading ? "Preparing..." : "Download my data"}
        </button>
      </section>

      <section className="card danger-zone">
        <h2>Delete account</h2>
        <p className="muted small">
          Deletes your account straight away, with the cases you created (and everything in them), the items you added to
          other cases, and the addresses you put on the watchlist. Audit log entries are kept without your name or email.
          This cannot be undone.
        </p>
        {!open ? (
          <button type="button" className="secondary danger" onClick={() => setOpen(true)}>
            <Icon name="trash" size={16} /> Delete my account...
          </button>
        ) : (
          <form className="form-grid" onSubmit={remove}>
            <label className="field">
              <span className="field-label">Your password</span>
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
            </label>
            <label className="field">
              <span className="field-label">Type DELETE to confirm</span>
              <input type="text" value={word} onChange={(e) => setWord(e.target.value)} autoComplete="off" required />
            </label>
            {error && <p className="notice error small" role="alert">{error}</p>}
            <div className="form-actions">
              <button type="button" className="secondary" onClick={() => { setOpen(false); setError(""); }}>Cancel</button>
              <button type="submit" className="secondary danger" disabled={busy}>{busy ? "Deleting..." : "Delete everything"}</button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
}
