# Security Policy

## Bedrohungsmodell

CBKS ist ein **local-first Single-User-System**. Es ist ausdrücklich **nicht**
dafür gebaut, im öffentlichen Internet oder als Multi-User-Dienst zu laufen.

Die Standardkonfiguration geht davon aus:

- Backend bindet ausschließlich `127.0.0.1` (siehe `backend/Dockerfile`,
  `docker/compose.yml`).
- LLM/VLM/Embeddings laufen lokal über Ollama — es verlassen keine Inhalte den
  Rechner.
- Der Nutzer ist der einzige Akteur mit Zugriff auf die Maschine.

## Wenn du davon abweichst

Sobald CBKS über `127.0.0.1` hinaus erreichbar wird, gelten diese Punkte:

| Thema | Status |
|---|---|
| **API-Key setzen** | `CBKS_API_KEY` ist optional; leer/unset = **komplett offene API**. Bei jeder Exposition über localhost hinaus zwingend setzen. |
| **Kein CSRF-Schutz** | `POST /documents` nimmt `multipart/form-data` an — ein Browser sendet das ohne Preflight. Eine beliebige Webseite kann so Requests an ein offenes lokales Backend schicken. Gegenmittel: `CBKS_API_KEY` setzen. |
| **Kein Host-Header-Check** | DNS-Rebinding gegen `127.0.0.1:8000` ist nicht abgewehrt. Gegenmittel: `CBKS_API_KEY` setzen oder Reverse-Proxy mit Host-Filter. |
| **Vault-Zugriff** | `POST /vault/scan` akzeptiert einen beliebigen Dateisystempfad und liest ihn in die Datenbank. Die Datei-Endpunkte (`/vault/file`, `/vault/rename`, `/vault/attachment`) sind per `vault_fs._resolve()` gegen Path-Traversal abgesichert, operieren aber mit den Rechten des Serverprozesses. |
| **TLS** | Nicht vorgesehen. Bei Exposition einen Reverse-Proxy davorsetzen. |
| **API-Key im Frontend** | Wird im `localStorage` abgelegt (`frontend/src/api/ApiKeyContext.tsx`). Für eine localhost-App vertretbar, für gehostete Deployments nicht. |

## Was als Lücke gilt

Ein Bericht ist willkommen bei:

- Path-Traversal oder Schreibzugriff außerhalb des Vault-Roots
- Umgehung der API-Key-Prüfung bei gesetztem `CBKS_API_KEY`
- SQL-Injection, Deserialisierungs- oder RCE-Pfade
- Preisgabe von Daten an externe Dienste (CBKS soll keine machen)

Kein gültiger Bericht: „Die API ist ohne `CBKS_API_KEY` offen." Das ist der
dokumentierte Standard für den local-first Betrieb.

## Meldung

Bitte **kein öffentliches Issue** für Sicherheitsprobleme. Nutze die
[GitHub Security Advisories](https://github.com/darkjive/cbks/security/advisories/new)
dieses Repos. Rückmeldung in der Regel innerhalb von 14 Tagen.

Dies ist ein Freizeit-/Experimentprojekt ohne SLA — es gibt keinen bezahlten
Support und keine garantierte Reaktionszeit.
