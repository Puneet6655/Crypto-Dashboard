import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useCases } from "../cases/CasesContext.jsx";
import { ITEM_TYPES, itemFromElement, sameItem, shortValue } from "../cases/caseItems.js";
import { navigate } from "../hooks/useHashRoute.js";
import Icon from "./Icon.jsx";
import { useToast } from "./Toasts.jsx";

// The "Add to case" menu. It opens from an "Add to case" button, or by right-clicking anything
// marked with data-case-type / data-case-value (graph nodes, addresses and transactions in lists).
export default function AddToCaseMenu() {
  const { picker, closePicker, openPicker, cases, canEdit, addItem, createCase } = useCases();
  const toast = useToast();
  const panelRef = useRef(null);
  const [newTitle, setNewTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [position, setPosition] = useState({ left: 0, top: 0 });

  // Right-click anywhere on a marked element opens the menu there.
  useEffect(() => {
    if (!canEdit) return;
    const onContextMenu = (event) => {
      const item = itemFromElement(event.target);
      if (!item) return;
      event.preventDefault();
      openPicker(item, { x: event.clientX, y: event.clientY });
    };
    document.addEventListener("contextmenu", onContextMenu);
    return () => document.removeEventListener("contextmenu", onContextMenu);
  }, [canEdit, openPicker]);

  // Reset the form each time the menu opens.
  useEffect(() => {
    setNewTitle("");
    setError("");
    setBusy(false);
  }, [picker]);

  // Keep the menu inside the window.
  useLayoutEffect(() => {
    if (!picker || !panelRef.current) return;
    const { width, height } = panelRef.current.getBoundingClientRect();
    setPosition({
      left: Math.max(8, Math.min(picker.x, window.innerWidth - width - 8)),
      top: Math.max(8, Math.min(picker.y, window.innerHeight - height - 8)),
    });
  }, [picker, cases.length]);

  // Close on Esc or on a click outside.
  useEffect(() => {
    if (!picker) return;
    const onKey = (event) => event.key === "Escape" && closePicker();
    const onDown = (event) => !panelRef.current?.contains(event.target) && closePicker();
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [picker, closePicker]);

  if (!picker) return null;
  const { item } = picker;
  const type = ITEM_TYPES[item.item_type] ?? ITEM_TYPES.other;
  const openCases = cases.filter((c) => c.status === "open");

  async function addTo(caseRow) {
    setBusy(true);
    setError("");
    try {
      const { created } = await addItem(caseRow.id, item);
      toast(created ? `Added to "${caseRow.title}"` : `Already in "${caseRow.title}"`, {
        tone: created ? "success" : "info",
        action: { label: "Open case", onClick: () => navigate(`/cases/${caseRow.id}`) },
      });
      closePicker();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  async function createWithItem(event) {
    event.preventDefault();
    if (!newTitle.trim()) return;
    setBusy(true);
    setError("");
    try {
      const created = await createCase({ title: newTitle.trim(), items: [item] });
      toast(`Created "${created.title}" with this ${type.short.toLowerCase()}`, {
        action: { label: "Open case", onClick: () => navigate(`/cases/${created.id}`) },
      });
      closePicker();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <div
      ref={panelRef}
      className="case-picker"
      style={position}
      role="dialog"
      aria-label="Add to case"
    >
      <div className="case-picker-head">
        <Icon name={type.icon} size={16} />
        <div>
          <div className="case-picker-title">Add {type.short.toLowerCase()} to a case</div>
          <div className="mono small muted" title={item.value}>{shortValue(item.value, 10)}</div>
        </div>
        <button type="button" className="ghost-icon" aria-label="Close" onClick={closePicker}>
          <Icon name="close" size={16} />
        </button>
      </div>

      {!canEdit ? (
        <p className="small muted">Only analysts and admins can add to cases.</p>
      ) : (
        <>
          <ul className="case-picker-list">
            {openCases.length === 0 && <li className="small muted">No open cases yet. Create one below.</li>}
            {openCases.map((c) => {
              const already = c.items.some((i) => sameItem(i, item));
              return (
                <li key={c.id}>
                  <button type="button" disabled={busy || already} onClick={() => addTo(c)} className="case-picker-row">
                    <span className={`priority-dot priority-${c.priority}`} aria-hidden="true" />
                    <span className="grow">{c.title}</span>
                    <span className="small muted">{already ? "already in" : `${c.item_count} item${c.item_count === 1 ? "" : "s"}`}</span>
                  </button>
                </li>
              );
            })}
          </ul>
          <form className="case-picker-new" onSubmit={createWithItem}>
            <input
              type="text"
              placeholder="New case title"
              value={newTitle}
              maxLength={120}
              onChange={(event) => setNewTitle(event.target.value)}
              aria-label="New case title"
            />
            <button type="submit" className="primary compact" disabled={busy || !newTitle.trim()}>
              <Icon name="plus" size={14} /> Create
            </button>
          </form>
        </>
      )}
      {error && <p className="small error-text">{error}</p>}
    </div>
  );
}

// A small button that opens the menu, showing how many cases already hold the item.
export function AddToCaseButton({ item, compact = false }) {
  const { openPicker, casesContaining, canEdit } = useCases();
  if (!item) return null;
  const inCases = casesContaining(item);
  if (!canEdit && inCases.length === 0) return null;
  return (
    <button
      type="button"
      className={`case-button ${inCases.length ? "in-case" : ""} ${compact ? "compact" : ""}`}
      title={inCases.length ? `In: ${inCases.map((c) => c.title).join(", ")}` : "Add to a case"}
      onClick={(event) => {
        const box = event.currentTarget.getBoundingClientRect();
        openPicker(item, { x: box.left, y: box.bottom + 6 });
      }}
    >
      <Icon name={inCases.length ? "cases" : "plus"} size={14} />
      {inCases.length ? `In ${inCases.length} case${inCases.length === 1 ? "" : "s"}` : "Add to case"}
    </button>
  );
}
