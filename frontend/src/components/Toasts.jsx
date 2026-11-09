import { createContext, useCallback, useContext, useMemo, useState } from "react";

// Short messages in the corner ("Added to case X"), each with an optional action button.
const ToastContext = createContext(() => {});
let nextId = 1;

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const dismiss = useCallback((id) => setToasts((old) => old.filter((t) => t.id !== id)), []);
  const show = useCallback(
    (message, { tone = "success", action, timeout = 4500 } = {}) => {
      const id = nextId++;
      setToasts((old) => [...old.slice(-3), { id, message, tone, action }]);
      setTimeout(() => dismiss(id), timeout);
    },
    [dismiss]
  );
  const value = useMemo(() => show, [show]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.tone}`}>
            <span>{t.message}</span>
            {t.action && (
              <button type="button" className="toast-action" onClick={() => { t.action.onClick(); dismiss(t.id); }}>
                {t.action.label}
              </button>
            )}
            <button type="button" className="toast-close" aria-label="Dismiss" onClick={() => dismiss(t.id)}>×</button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
