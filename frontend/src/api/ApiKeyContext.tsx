import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { setApiKey } from "./client";

const STORAGE_KEY = "cbks-api-key";

interface ApiKeyContextValue {
  apiKey: string | null;
  setKey: (key: string) => void;
}

const ApiKeyContext = createContext<ApiKeyContextValue | null>(null);

export function ApiKeyProvider({ children }: { children: ReactNode }) {
  const [apiKey, setKeyState] = useState<string | null>(
    () => localStorage.getItem(STORAGE_KEY)
  );

  useEffect(() => {
    setApiKey(apiKey);
  }, [apiKey]);

  const setKey = (key: string) => {
    localStorage.setItem(STORAGE_KEY, key);
    setKeyState(key);
  };

  return (
    <ApiKeyContext.Provider value={{ apiKey, setKey }}>
      {children}
    </ApiKeyContext.Provider>
  );
}

export function useApiKey(): ApiKeyContextValue {
  const ctx = useContext(ApiKeyContext);
  if (!ctx) {
    throw new Error("useApiKey muss innerhalb von ApiKeyProvider verwendet werden");
  }
  return ctx;
}
