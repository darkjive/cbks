import { useState } from "react";
import { apiFetch } from "../api/client";
import { useToast } from "./Toast";
import type { SearchHit } from "../api/types";

interface Props {
  onResults: (hits: SearchHit[]) => void;
  // Aktuelle Trefferzahl aus dem Parent (Single Source of Truth). So bleibt der
  // Zaehler korrekt, wenn Treffer ausserhalb der Suche wegfallen (z.B. Node
  // geloescht), statt auf einem veralteten lokalen count zu haengen.
  resultCount: number;
}

export function SearchBar({ onResults, resultCount }: Props) {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const { pushError, pushToast } = useToast();

  const clear = () => {
    setQuery("");
    onResults([]);
  };

  const runSearch = async () => {
    if (!query.trim()) {
      clear();
      return;
    }
    setLoading(true);
    try {
      const hits = await apiFetch<SearchHit[]>(`/search?q=${encodeURIComponent(query.trim())}`);
      onResults(hits);
      if (hits.length === 0) pushToast("Keine Treffer", "info");
    } catch (err) {
      pushError(err, "Suche fehlgeschlagen");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="search-bar">
      <div>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && runSearch()}
          placeholder="Suche..."
        />
        <button onClick={runSearch} disabled={loading}>
          {loading ? "…" : "Suchen"}
        </button>
        {(resultCount > 0 || query) && (
          <button className="search-clear" onClick={clear} title="Suche zurücksetzen">
            ✕
          </button>
        )}
      </div>
      {resultCount > 0 && (
        <span className="search-count">{resultCount} Treffer</span>
      )}
    </div>
  );
}
