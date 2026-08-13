import { useState } from "react";
import { useApiKey } from "../api/ApiKeyContext";

export function ApiKeyPrompt() {
  const { setKey } = useApiKey();
  const [value, setValue] = useState("");

  return (
    <form
      className="api-key-prompt"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) {
          setKey(value.trim());
        }
      }}
    >
      <label htmlFor="api-key-input">CBKS_API_KEY (falls konfiguriert):</label>
      <input
        id="api-key-input"
        type="password"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="leer lassen, falls kein Key gesetzt ist"
      />
      <button type="submit">Speichern</button>
    </form>
  );
}
