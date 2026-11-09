import Icon from "./Icon.jsx";

// The title row at the top of every page.
export default function PageHeader({ icon, title, subtitle, children }) {
  return (
    <header className="page-header">
      <div className="page-title">
        {icon && <span className="page-icon"><Icon name={icon} size={26} /></span>}
        <div>
          <h1>{title}</h1>
          {subtitle && <p className="muted">{subtitle}</p>}
        </div>
      </div>
      {children && <div className="page-actions">{children}</div>}
    </header>
  );
}

// "3 minutes ago", "2 days ago". Falls back to the date for anything older than a month.
export function timeAgo(value) {
  if (!value) return "never";
  const then = new Date(value);
  const seconds = (Date.now() - then.getTime()) / 1000;
  if (Number.isNaN(seconds)) return String(value);
  if (seconds < 45) return "just now";
  const ago = (n, unit) => `${n} ${unit}${n === 1 ? "" : "s"} ago`;
  if (seconds < 3600) return ago(Math.max(1, Math.round(seconds / 60)), "minute");
  if (seconds < 86400) return ago(Math.round(seconds / 3600), "hour");
  if (seconds < 30 * 86400) return ago(Math.round(seconds / 86400), "day");
  return formatDate(value);
}

export function formatDate(value, withTime = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString(undefined, withTime
    ? { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
    : { year: "numeric", month: "short", day: "numeric" });
}
