import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { ApiError } from "../api/client";

type ToastType = "error" | "success" | "info";

interface ToastItem {
  id: number;
  message: string;
  type: ToastType;
}

interface ToastContextValue {
  pushToast: (message: string, type?: ToastType) => void;
  pushError: (err: unknown, fallback?: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

function extractMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const detail =
      typeof err.body === "object" && err.body !== null && "detail" in err.body
        ? String((err.body as { detail: unknown }).detail)
        : null;
    return detail ?? `API-Fehler ${err.status}`;
  }
  if (err instanceof Error) return err.message;
  return "Unbekannter Fehler";
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(0);

  const remove = useCallback((id: number) => {
    setToasts((list) => list.filter((t) => t.id !== id));
  }, []);

  const pushToast = useCallback(
    (message: string, type: ToastType = "info") => {
      const id = nextId.current++;
      setToasts((list) => [...list, { id, message, type }]);
      window.setTimeout(() => remove(id), 4500);
    },
    [remove]
  );

  const pushError = useCallback(
    (err: unknown, fallback = "Aktion fehlgeschlagen") => {
      pushToast(extractMessage(err) || fallback, "error");
    },
    [pushToast]
  );

  return (
    <ToastContext.Provider value={{ pushToast, pushError }}>
      {children}
      <div className="toast-container">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.type}`} onClick={() => remove(t.id)}>
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast muss innerhalb von ToastProvider verwendet werden");
  return ctx;
}
