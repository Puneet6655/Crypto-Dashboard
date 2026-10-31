import { ROLE_LABELS, useAuth } from "../auth/AuthContext.jsx";
import { useCases } from "../cases/CasesContext.jsx";
import Icon from "./Icon.jsx";

// The pages in the side panel. "role" hides a page from people without that role.
export const PAGES = [
  { id: "investigate", label: "Investigate", icon: "investigate", hint: "Look up addresses and transactions" },
  { id: "cases", label: "Cases", icon: "cases", hint: "Group what you want to follow up" },
  { id: "alerts", label: "Live alerts", icon: "bell", hint: "Case and OFAC addresses moving money now" },
  { id: "status", label: "Status", icon: "status", hint: "Live lookups, database, sanction lists" },
  { id: "sources", label: "Sources", icon: "sources", hint: "Data and software we rely on" },
  { id: "bigquery", label: "BigQuery refresh", icon: "bigquery", hint: "Load new transaction data", badge: "Dev" },
  { id: "users", label: "Users", icon: "users", hint: "Accounts, roles and audit log", role: "admin" },
];

export default function Sidebar({ page, open, onClose, unseenAlerts = 0 }) {
  const { user, can, signOut } = useAuth();
  const { cases } = useCases();
  const openCases = cases.filter((c) => c.status === "open").length;

  return (
    <>
      <aside className={`sidebar ${open ? "open" : ""}`} aria-label="Pages">
        <a className="brand" href="#/investigate" onClick={onClose}>
          <span className="brand-mark"><Icon name="brand" size={22} /></span>
          <span>
            <span className="brand-name">Payments Intelligence</span>
            <span className="brand-sub">Bitcoin risk dashboard</span>
          </span>
        </a>

        <nav className="nav">
          {PAGES.filter((p) => !p.role || can(p.role)).map((p) => (
            <a
              key={p.id}
              href={`#/${p.id}`}
              className={`nav-item ${page === p.id ? "active" : ""}`}
              aria-current={page === p.id ? "page" : undefined}
              title={p.hint}
              onClick={onClose}
            >
              <Icon name={p.icon} size={20} />
              <span className="grow">{p.label}</span>
              {p.id === "cases" && openCases > 0 && <span className="nav-count">{openCases}</span>}
              {p.id === "alerts" && unseenAlerts > 0 && <span className="nav-count nav-count-alert">{unseenAlerts}</span>}
              {p.badge && <span className="nav-badge">{p.badge}</span>}
            </a>
          ))}
        </nav>

        {user && (
          <div className="sidebar-user">
            <a href="#/account" className={`user-chip ${page === "account" ? "active" : ""}`} onClick={onClose} title="Your account">
              <span className="avatar" aria-hidden="true">{initials(user.display_name)}</span>
              <span className="user-text">
                <span className="user-name">{user.display_name}</span>
                <span className={`role-badge role-${user.role}`}>{ROLE_LABELS[user.role] ?? user.role}</span>
              </span>
            </a>
            {user.id !== null && (
              <button type="button" className="ghost-icon light" title="Sign out" aria-label="Sign out" onClick={signOut}>
                <Icon name="logout" size={18} />
              </button>
            )}
          </div>
        )}
      </aside>
      {open && <div className="sidebar-scrim" onClick={onClose} aria-hidden="true" />}
    </>
  );
}

function initials(name = "") {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return (parts[0]?.[0] ?? "?").toUpperCase() + (parts[1]?.[0] ?? "").toUpperCase();
}
