import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../api/client";
import { useToast } from "./Toast";
import type { VaultScanState } from "../api/types";

interface Props {
  onIngested: () => void;
}

type Tab = "vault" | "note" | "file";

const POLL_INTERVAL_MS = 1000;

export function UploadForm({ onIngested }: Props) {
  const [activeTab, setActiveTab] = useState<Tab>("vault");
  const [noteText, setNoteText] = useState("");
  const [noteBusy, setNoteBusy] = useState(false);
  const [fileBusy, setFileBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [vaultPath, setVaultPath] = useState("");
  const [vaultJobId, setVaultJobId] = useState<string | null>(null);
  const [vaultState, setVaultState] = useState<VaultScanState | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { pushError, pushToast } = useToast();

  useEffect(() => {
    apiFetch<{ path: string }>("/vault/default-path")
      .then((result) => setVaultPath(result.path))
      .catch(() => {
        // Vorbefüllung ist best-effort; ohne Default bleibt das Feld leer.
      });
  }, []);

  useEffect(() => {
    if (!vaultJobId) return;
    let cancelled = false;
    const interval = window.setInterval(async () => {
      try {
        const state = await apiFetch<VaultScanState>(`/vault/scan/${vaultJobId}`);
        if (cancelled) return;
        setVaultState(state);
        if (state.done) {
          window.clearInterval(interval);
          setVaultJobId(null);
          if (state.error) {
            pushToast(`Vault-Scan fehlgeschlagen: ${state.error}`, "error");
          } else {
            if (state.total === 0) {
              pushToast("Keine Dateien gefunden", "info");
            } else {
              pushToast(
                `✓ ${state.processed} importiert, ⊘ ${state.duplicates} Duplikate, ✕ ${state.failed} Fehler`,
                "success"
              );
            }
            onIngested();
          }
        }
      } catch (err) {
        if (cancelled) return;
        window.clearInterval(interval);
        setVaultJobId(null);
        pushError(err, "Vault-Scan-Status konnte nicht abgerufen werden");
      }
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [vaultJobId, onIngested, pushError, pushToast]);

  const startVaultScan = async () => {
    if (!vaultPath.trim() || vaultJobId !== null) return;
    setVaultState(null);
    try {
      const result = await apiFetch<{ job_id: string }>("/vault/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: vaultPath }),
      });
      setVaultJobId(result.job_id);
    } catch (err) {
      pushError(err, "Vault-Scan konnte nicht gestartet werden");
    }
  };

  const submitNote = async () => {
    if (!noteText.trim()) return;
    setNoteBusy(true);
    try {
      const result = await apiFetch<{ duplicate?: boolean; processed?: number; failed?: number }>(
        "/notes",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: noteText }),
        }
      );
      if (result?.duplicate) {
        pushToast("Notiz bereits vorhanden (Duplikat)", "info");
      } else {
        pushToast(
          `Verarbeitet: ${result?.processed ?? 0}, Fehler: ${result?.failed ?? 0}`,
          "success"
        );
      }
      setNoteText("");
      onIngested();
    } catch (err) {
      pushError(err, "Notiz konnte nicht gespeichert werden");
    } finally {
      setNoteBusy(false);
    }
  };

  const submitFile = async (file: File) => {
    setFileBusy(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const result = await apiFetch<{ duplicate?: boolean; processed?: number; failed?: number }>(
        "/documents",
        { method: "POST", body: formData }
      );
      if (result?.duplicate) {
        pushToast("Dokument bereits vorhanden (Duplikat)", "info");
      } else {
        pushToast(
          `Verarbeitet: ${result?.processed ?? 0}, Fehler: ${result?.failed ?? 0}`,
          "success"
        );
      }
      onIngested();
    } catch (err) {
      pushError(err, "Datei konnte nicht hochgeladen werden");
    } finally {
      setFileBusy(false);
    }
  };

  return (
    <div className="upload-form">
      <div className="upload-tabs">
        <button
          className={`upload-tab ${activeTab === "vault" ? "active" : ""}`}
          onClick={() => setActiveTab("vault")}
        >
          Vault
        </button>
        <button
          className={`upload-tab ${activeTab === "note" ? "active" : ""}`}
          onClick={() => setActiveTab("note")}
        >
          Notiz
        </button>
        <button
          className={`upload-tab ${activeTab === "file" ? "active" : ""}`}
          onClick={() => setActiveTab("file")}
        >
          Datei
        </button>
      </div>

      {activeTab === "vault" && (
        <div className="upload-tab-content">
          <input
            type="text"
            value={vaultPath}
            onChange={(e) => setVaultPath(e.target.value)}
            placeholder="/pfad/zum/vault"
          />
          <button onClick={startVaultScan} disabled={vaultJobId !== null}>
            {vaultJobId !== null ? "Scan läuft…" : "Vault scannen & importieren"}
          </button>
          {vaultState !== null && (
            <>
              <div className="vault-progress">
                <div className="dist-track">
                  <div
                    className="dist-fill"
                    style={{
                      width:
                        vaultState.total > 0
                          ? `${(vaultState.scanned / vaultState.total) * 100}%`
                          : "0%",
                      background: "#6C8EF5",
                    }}
                  />
                </div>
                <span className="dist-value">
                  {vaultState.scanned}/{vaultState.total}
                </span>
              </div>
              {vaultState.processing_total > 0 && (
                <div className="vault-progress">
                  <div className="dist-track">
                    <div
                      className="dist-fill"
                      style={{
                        width: `${(vaultState.processing_done / vaultState.processing_total) * 100}%`,
                        background: "#8EF5C8",
                      }}
                    />
                  </div>
                  <span className="dist-value">
                    Verarbeite {vaultState.processing_done}/{vaultState.processing_total}
                  </span>
                </div>
              )}
              <div className="vault-stats">
                <span className="stat-inline">
                  ✓ <strong>{vaultState.processed}</strong> importiert
                </span>
                <span className="stat-inline">
                  ⊘ <strong>{vaultState.duplicates}</strong> Duplikate
                </span>
                <span className="stat-inline">
                  ✕ <strong>{vaultState.failed}</strong> Fehler
                </span>
              </div>
            </>
          )}
        </div>
      )}

      {activeTab === "note" && (
        <div className="upload-tab-content">
          <div className="upload-row">
            <input
              type="text"
              value={noteText}
              onChange={(e) => setNoteText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submitNote()}
              placeholder="Notiz eintippen..."
            />
            <button onClick={submitNote} disabled={noteBusy}>
              {noteBusy ? "…" : "Speichern"}
            </button>
          </div>
        </div>
      )}

      {activeTab === "file" && (
        <div className="upload-tab-content">
          <div
            className={`dropzone ${dragOver ? "dragover" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              const file = e.dataTransfer.files?.[0];
              if (file) submitFile(file);
            }}
            onClick={() => fileInputRef.current?.click()}
          >
            {fileBusy ? "Wird hochgeladen…" : "Datei hierher ziehen oder klicken"}
          </div>
          <input
            ref={fileInputRef}
            type="file"
            hidden
            disabled={fileBusy}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) submitFile(file);
              e.target.value = "";
            }}
          />
        </div>
      )}
    </div>
  );
}
