import { apiFetch } from "../api/client.js";
import { ROLE_LABELS, useAuth } from "../auth/AuthContext.jsx";
import { useApi } from "../hooks/useApi.js";
import PageHeader, { formatDate, timeAgo } from "../components/PageHeader.jsx";
import Icon from "../components/Icon.jsx";
import { useToast } from "../components/Toasts.jsx";

const ACTION_LABELS = {
  "user.signup": "signed up",
  "user.changed": "changed a user",
  "user.password_changed": "changed their password",
  "case.created": "created a case",
  "case.closed": "closed a case",
  "case.reopened": "reopened a case",
  "case.deleted": "deleted a case",
  "case.item_removed": "removed an item from a case",
  "source.added": "added a source",
  "source.edited": "edited a source",
  "source.removed": "removed a source",
  "bigquery.estimate": "estimated a BigQuery load",
};

// Admin only: who has an account, what they may do, and what changed recently.
export default function UsersPage() {
  const { user: me } = useAuth();
  const toast = useToast();
  const users = useApi("/api/admin/users");
  const audit = useApi("/api/admin/audit?limit=60");

  async function change(user, changes, message) {
    try {
      await apiFetch(`/api/admin/users/${user.id}`, { method: "PATCH", body: changes });
      toast(message);
      users.reload();
      audit.reload();
    } catch (err) {
      toast(err.message, { tone: "error" });
    }
  }

  const rows = users.data ?? [];
  const counts = rows.reduce((acc, u) => ({ ...acc, [u.role]: (acc[u.role] ?? 0) + 1 }), {});

  return (
    <>
      <PageHeader icon="users" title="Users" subtitle="Accounts and roles. New sign-ups are viewers; give analyst or admin access here." />

      <div className="stat-row">
        {["viewer", "analyst", "admin"].map((role) => (
          <div key={role} className="card stat">
            <span className={`role-badge role-${role}`}>{ROLE_LABELS[role]}s</span>
            <strong>{counts[role] ?? 0}</strong>
          </div>
        ))}
      </div>

      <section className="card">
        <h2>Accounts</h2>
        {users.error && <p className="notice error">{users.error}</p>}
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>Name</th><th>Email</th><th>Role</th><th>State</th><th>Joined</th><th>Last sign-in</th></tr>
            </thead>
            <tbody>
              {rows.map((u) => (
                <tr key={u.id} className={u.disabled ? "row-disabled" : ""}>
                  <td>{u.display_name}{u.id === me.id && <span className="tag">you</span>}</td>
                  <td className="small">{u.email}</td>
                  <td>
                    <select value={u.role} className="auto" aria-label={`Role for ${u.email}`}
                      onChange={(e) => change(u, { role: e.target.value }, `${u.display_name} is now ${ROLE_LABELS[e.target.value].toLowerCase()}`)}>
                      {Object.entries(ROLE_LABELS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
                    </select>
                  </td>
                  <td>
                    <button type="button" className={`secondary compact ${u.disabled ? "" : "danger"}`} disabled={u.id === me.id}
                      title={u.id === me.id ? "You cannot turn off your own account" : ""}
                      onClick={() => change(u, { disabled: !u.disabled }, u.disabled ? `${u.display_name} can sign in again` : `${u.display_name} is turned off and signed out`)}>
                      {u.disabled ? "Turn on" : "Turn off"}
                    </button>
                  </td>
                  <td className="small muted">{formatDate(u.created_at)}</td>
                  <td className="small muted">{u.last_login_at ? timeAgo(u.last_login_at) : "never"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="small muted">
          Turning an account off signs it out everywhere. The last admin cannot be demoted or turned off.
          Passwords can be reset from the command line: <code>python -m app.auth reset-password email</code>.
        </p>
      </section>

      <section className="card">
        <div className="section-head">
          <h2>Audit log</h2>
          <button type="button" className="ghost-icon" title="Refresh" aria-label="Refresh" onClick={audit.reload}><Icon name="refresh" size={16} /></button>
        </div>
        {audit.error && <p className="notice error">{audit.error}</p>}
        <ul className="timeline">
          {(audit.data ?? []).map((a) => (
            <li key={a.id}>
              <span className="timeline-dot" aria-hidden="true" />
              <div>
                <strong>{a.user_email ?? "system"}</strong> {ACTION_LABELS[a.action] ?? a.action}
                {a.target && <span className="muted"> · {a.target}</span>}
                {describeDetail(a)}
                <div className="small muted" title={formatDate(a.at, true)}>{timeAgo(a.at)}</div>
              </div>
            </li>
          ))}
          {audit.data && !audit.data.length && <li className="muted small">Nothing yet.</li>}
        </ul>
      </section>
    </>
  );
}

function describeDetail(entry) {
  const d = entry.detail ?? {};
  if (entry.action === "user.changed") {
    const parts = [];
    if (d.role) parts.push(`role ${d.before_role} → ${d.role}`);
    if (d.disabled !== undefined) parts.push(d.disabled ? "turned off" : "turned on");
    return parts.length ? <span className="muted"> ({parts.join(", ")})</span> : null;
  }
  if (entry.action === "source.edited") {
    const fields = Object.keys(d);
    return fields.length ? <span className="muted"> (changed {fields.join(", ")})</span> : null;
  }
  if (entry.action === "bigquery.estimate") return <span className="muted"> ({d.start} to {d.end}, {d.gb} GB)</span>;
  if (d.title) return <span className="muted"> “{d.title}”</span>;
  return null;
}
