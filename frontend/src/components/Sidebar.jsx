import { ROLE_LABELS, useAuth } from "../auth/AuthContext.jsx";
import { useCases } from "../cases/CasesContext.jsx";
import Icon from "./Icon.jsx";
import { WEB3_DESIGN } from "../config/w3Design.js";

// The pages in the side panel. "role" hides a page from people without that role.
export const PAGES = [
  { id: "investigate", label: "Investigate", icon: "investigate", hint: "Look up addresses and transactions" },
  { id: "graph", label: "Graph explorer", icon: "graph", hint: "Advanced graph mode: thousands of nodes, expand step by step" },
  { id: "cases", label: "Cases", icon: "cases", hint: "Group what you want to follow up" },
  { id: "alerts", label: "Live alerts", icon: "bell", hint: "Case and OFAC addresses moving money now" },
  { id: "x402", label: "x402 payments", icon: "payment", hint: "AI-agent USDC payments: risky servers and flagged payments" },
  { id: "models", label: "Models", icon: "shield", hint: "Random Forest vs XGBoost, offline benchmark and SHAP", role: "admin" },
  { id: "status", label: "Status", icon: "status", hint: "Live lookups, database, sanction lists" },
  { id: "sources", label: "Sources", icon: "sources", hint: "Data and software we rely on" },
  { id: "bigquery", label: "BigQuery refresh", icon: "bigquery", hint: "Load new transaction data", badge: "Dev", role: "admin" },
  { id: "users", label: "Users", icon: "users", hint: "Accounts, roles and audit log", role: "admin" },
];

// reveal (Web3 mode only): { shown, pinned, togglePin, show, hide } from w3/useEdgeReveal.js.
export default function Sidebar({ page, open, onClose, unseenAlerts = 0, reveal = null }) {
  const { user, can, signOut } = useAuth();
  const { cases } = useCases();
  const openCases = cases.filter((c) => c.status === "open").length;

  return (
    <>
      <aside className={`sidebar ${open ? "open" : ""} ${reveal ? "w3-reveal" : ""} ${reveal?.shown ? "w3-shown" : ""}`} aria-label="Pages"
        onFocus={reveal ? reveal.show : undefined}
        onBlur={reveal ? (e) => { if (!e.currentTarget.contains(e.relatedTarget)) reveal.hide(); } : undefined}>
        {/* Web3 design: the logo and the Home item open the animated overview (#/overview). */}
        <a className="brand" href={WEB3_DESIGN ? "#/overview" : "#/investigate"} onClick={onClose}
          title={WEB3_DESIGN ? "Home" : undefined}>
          <span className="brand-mark"><Icon name="brand" size={22} /></span>
          <span>
            <span className="brand-name">Blockchain Intelligence</span>
            <span className="brand-sub">{reveal ? "Multi-chain risk" : "Bitcoin risk dashboard"}</span>
          </span>
        </a>
        {reveal && (
          <button type="button" className={`ghost-icon light w3-pin ${reveal.pinned ? "on" : ""}`} onClick={reveal.togglePin}
            title={reveal.pinned ? "Unpin: hide the menu until the mouse reaches the left edge" : "Pin: keep the menu open"}
            aria-pressed={reveal.pinned} aria-label="Keep the menu open">
            <PinIcon />
          </button>
        )}

        <nav className="nav">
          {WEB3_DESIGN && (
            <a href="#/overview" className={`nav-item ${page === "overview" ? "active" : ""}`}
              aria-current={page === "overview" ? "page" : undefined} title="Animated overview" onClick={onClose}>
              <Icon name="home" size={20} />
              <span className="grow">Home</span>
            </a>
          )}
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

function PinIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 17v5M9 3h6l-1 6 3 3v2H7v-2l3-3z" />
    </svg>
  );
}
