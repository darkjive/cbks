# CBKS Phase 1 (Fundament) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Das technische Fundament aus CBKS_SPEC_v1.1.md §8 "Phase 1: Fundament (Woche 1)" herstellen: GPU/ROCm-Stack lauffähig, Verzeichnisstruktur, Docker-Infrastruktur, Datenverzeichnis auf einem sicheren Dateisystem, Backup-System aktiv, Modellgröße per Benchmark entschieden.

**Architecture:** Reines Infrastruktur-Setup ohne Anwendungslogik (die kommt in Phase 2).

> **Abweichung von Spec v1.1 §6.3/§6.6/§7.4, festgelegt am 2026-07-04 nach Host-Prüfung:**
> - Auf diesem Host läuft bereits ein natives Ollama als User-systemd-Service (`~/.config/systemd/user/ollama.service`, seit 2026-07-03 aktiv, ROCm bereits funktionsfähig, ~10 andere Modelle geladen). CBKS startet **keinen eigenen Ollama-Docker-Container** — der `cbks-backend`-Container läuft mit `network_mode: host` und spricht das native Ollama über `http://127.0.0.1:11434` an.
> - Es existiert kein `/mnt/hdd`. Die einzigen großen Laufwerke (`/mnt/external`, `/mnt/external2`) sind exFAT — ungeeignet für Docker-Root (Storage-Driver braucht ein natives Unix-Dateisystem) und riskant für SQLite (unzuverlässiges Datei-Locking über FUSE-exfat). `cbks_data` (SQLite, FAISS, Backups) liegt stattdessen nativ unter `$REPO/data` (btrfs).
> - Task 8 der Spec-Roadmap ("Docker-Root auf HDD verlagern") entfällt ersatzlos — Docker-Root bleibt auf der NVMe (`/var/lib/docker`, Standard).
> - Ollama-Modelle bleiben am bestehenden Ort (`/mnt/external/AI/ollama/models`, exFAT) — unkritisch, da rein lesende Modell-Dateien ohne Locking-Bedarf.
>
> **Nachträgliche Abweichung, festgelegt am 2026-07-04 nach Task 6:** `cbks_data` liegt nicht mehr unter `$HOME/cbks_data`, sondern repo-lokal unter `$REPO/data` (Nutzerentscheidung — Datenverzeichnis soll im Projektordner statt daneben liegen). Beide Pfade liegen auf demselben btrfs-`/home`-Mount, die Locking-Begründung aus dem vorherigen Punkt gilt unverändert. `data/` ist bereits in `.gitignore` erfasst (kein neuer Eintrag nötig).

**Tech Stack:** Docker Compose (nur Backend-Container, `network_mode: host`), natives Ollama (bereits installiert), Bash, cron, sqlite3 (CLI), rsync.

## Global Constraints

- Docker-Compose-Datei **ohne** `version: '3.8'` (veraltetes Feld, spec Änderung #13).
- Kein eigener Ollama-Container — natives Ollama auf dem Host wird über `http://127.0.0.1:11434` angesprochen (`network_mode: host` im Backend-Service).
- Backend bindet nur an `127.0.0.1:8000`, niemals `0.0.0.0` (mit `network_mode: host` heißt das: Uvicorn-CMD explizit `--host 127.0.0.1`, da `ports:`-Mapping in diesem Modus wirkungslos ist).
- Docker-Pruning **ohne** `-a --volumes` (Datenverlust-Falle aus v1.0, spec Änderung #5) — wöchentlich, nicht täglich.
- `cbks_data` (SQLite, FAISS, Backups) liegt unter `$REPO/data` — **nicht** `/mnt/hdd` (existiert nicht) und **nicht** auf einer exFAT-Platte.
- Docker-Root bleibt auf der NVMe-SSD (`/var/lib/docker`, Standardpfad) — keine Verlagerung.
- Kein `--reload` im Backend-Container (Entwicklungsmodus, spec Änderung #13).
- Modellgröße (Qwen3 4B/8B/14B) wird **nicht** vorab festgelegt, sondern in Task 9 per Benchmark entschieden.
- Jede riskante Host-Operation braucht vor Ausführung eine explizite Bestätigung des Nutzers — nicht automatisch ausführen.

---

### Task 1: Verzeichnisstruktur anlegen

**Files:**
- Create: `backend/main.py` (leeres Platzhalter-Modul mit `# CBKS FastAPI entrypoint — implementiert ab Phase 2`)
- Create: `backend/models/.gitkeep`, `backend/services/.gitkeep`, `backend/services/agents/.gitkeep`, `backend/storage/.gitkeep`, `backend/api/.gitkeep`, `backend/utils/.gitkeep`, `backend/tests/.gitkeep`
- Create: `frontend/.gitkeep`
- Create: `docker/.gitkeep`
- Create: `docs/API.md` (Ein-Zeiler: `# API-Dokumentation — folgt ab Phase 3`)
- Create: `.gitignore`

**Interfaces:**
- Produces: Verzeichnis-Layout gemäß Spec §6.1, das alle folgenden Tasks referenzieren (`backend/requirements.txt`, `docker/compose.yml`, etc.)

- [ ] **Step 1: Verzeichnisse und Platzhalter anlegen**

```bash
mkdir -p backend/{models,services/agents,storage,api,utils,tests}
mkdir -p frontend docker
touch backend/models/.gitkeep backend/services/.gitkeep backend/services/agents/.gitkeep \
      backend/storage/.gitkeep backend/api/.gitkeep backend/utils/.gitkeep backend/tests/.gitkeep \
      frontend/.gitkeep docker/.gitkeep
printf '# CBKS FastAPI entrypoint — implementiert ab Phase 2\n' > backend/main.py
printf '# API-Dokumentation — folgt ab Phase 3\n' > docs/API.md
```

- [ ] **Step 2: `.gitignore` schreiben**

```
__pycache__/
*.pyc
.venv/
venv/
data/
*.db
*.log
.env
```

- [ ] **Step 3: Verifizieren**

```bash
find backend frontend docker -type f | sort
```

Erwartet: alle oben genannten Dateien erscheinen.

- [ ] **Step 4: Git-Repo initialisieren und committen**

```bash
git init
git add backend frontend docker docs/API.md .gitignore
git commit -m "chore: Projektgerüst gemäß CBKS_SPEC_v1.1 §6.1 anlegen"
```

---

### Task 2: requirements.txt

**Files:**
- Create: `backend/requirements.txt`

**Interfaces:**
- Consumes: nichts
- Produces: Python-Abhängigkeitsliste, die Task 3 (Dockerfile) per `pip install -r requirements.txt` konsumiert

- [ ] **Step 1: Datei exakt gemäß Spec §6.2 schreiben**

```
# Core
fastapi==0.110.0
uvicorn==0.29.0
pydantic==2.6.0
python-multipart==0.0.6
typer==0.12.0                    # CLI – NEU

# Graph
networkx==3.2.1
# sqlite3: Standardbibliothek, KEIN pip-Paket
# asyncio: Standardbibliothek, KEIN pip-Paket

# Vektorsuche
faiss-cpu==1.8.0
# Hinweis: faiss-gpu existiert nur für CUDA (NVIDIA).
# Für die RX 6900 XT (ROCm) ist faiss-cpu die richtige Wahl –
# der Ryzen 9 5900X ist für persönliche Datenmengen mehr als ausreichend.

# LLM & Embeddings – beides über Ollama (ein Inferenz-Stack)
ollama==0.2.0
# bge-m3 wird als Ollama-Modell geladen, nicht als pip-Paket:
#   ollama pull bge-m3

# Parsing
pymupdf==1.24.0
markdown==3.6
beautifulsoup4==4.12.2

# Monitoring & Utils
apscheduler==3.10.4
python-json-logger==2.0.7
httpx==0.27.0
```

- [ ] **Step 2: Verifizieren — Abhängigkeiten sind auflösbar**

```bash
cd backend && python3 -m venv /tmp/cbks_venv_check && /tmp/cbks_venv_check/bin/pip install -r requirements.txt && rm -rf /tmp/cbks_venv_check
```

Erwartet: `Successfully installed ...` ohne Fehler. Kein `sqlite3`/`asyncio` in der Liste (das würde die Installation abbrechen, spec Änderung #1).

- [ ] **Step 3: Commit**

```bash
git add backend/requirements.txt
git commit -m "chore: requirements.txt gemäß Spec §6.2 anlegen"
```

---

### Task 3: Backend Dockerfile

**Files:**
- Create: `backend/Dockerfile`

**Interfaces:**
- Consumes: `backend/requirements.txt` (Task 2), `backend/main.py` (Task 1)
- Produces: Image `cbks-backend:local`, das Task 4 (compose.yml) baut

- [ ] **Step 1: Dockerfile exakt gemäß Spec §6.3 schreiben**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Nur Build-Werkzeuge – KEIN ROCm nötig:
# Das Backend spricht mit Ollama nur per HTTP, GPU-Zugriff hat allein der Ollama-Container.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m cbksuser && chown -R cbksuser /app
USER cbksuser

# Kein --reload im Betrieb (Entwicklungsmodus).
# Host statt 0.0.0.0: Container läuft mit network_mode: host (Task 4, um
# das native Ollama auf 127.0.0.1:11434 zu erreichen), daher muss Uvicorn
# selbst auf 127.0.0.1 binden, um "nur localhost" zu garantieren.
CMD ["uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"]
```

- [ ] **Step 2: Image bauen und verifizieren**

```bash
docker build -t cbks-backend:local ./backend
docker run --rm cbks-backend:local python3 -c "import fastapi, uvicorn, networkx, faiss, ollama; print('OK')"
```

Erwartet: `OK` — alle Kernabhängigkeiten importierbar im Container.

- [ ] **Step 3: Commit**

```bash
git add backend/Dockerfile
git commit -m "chore: Backend-Dockerfile gemäß Spec §6.3 anlegen"
```

---

### Task 4: Docker Compose (nur Backend, natives Ollama)

**Files:**
- Create: `docker/compose.yml`
- Create: `docker/.env.example`

**Interfaces:**
- Consumes: Image `cbks-backend:local` (Task 3), natives Ollama auf `http://127.0.0.1:11434` (bereits installiert und laufend — kein Task in diesem Plan startet es), Verzeichnis `$REPO/data` (muss vor dem ersten Start existieren, siehe Step 3)
- Produces: laufender Service `cbks-backend` mit Host-Netzwerk, den Task 9 (Benchmark) und Phase 2 nutzen

Kein `ollama`-Service in dieser Compose-Datei — begründet im Header "Abweichung von Spec v1.1" dieses Plans: natives Ollama läuft bereits mit funktionierendem ROCm-Zugriff.

- [ ] **Step 1: `docker/compose.yml` schreiben**

```yaml
services:
  cbks-backend:
    build:
      context: ../backend
      dockerfile: Dockerfile
    container_name: cbks-backend
    network_mode: host          # Damit 127.0.0.1:11434 (natives Ollama) erreichbar ist
    volumes:
      - $REPO/data:/data
    environment:
      - OLLAMA_HOST=http://127.0.0.1:11434
      - DATABASE_PATH=/data/cbks.db
      - FAISS_PATH=/data/faiss_index
      - SNAPSHOT_PATH=/data/snapshots
      - BACKUP_PATH=/data/backups
    restart: unless-stopped
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

- [ ] **Step 2: `docker/.env.example` anlegen (Dokumentation der erwarteten Host-Pfade)**

```
# Vor dem ersten Start muss dieses Verzeichnis auf dem Host existieren:
#   $REPO/data
#
# Natives Ollama muss bereits laufen (systemctl --user status ollama)
# und auf 127.0.0.1:11434 erreichbar sein — dieses Compose-Setup startet
# keinen eigenen Ollama-Container.
```

- [ ] **Step 3: Datenverzeichnis anlegen und Compose-Datei syntaktisch validieren**

```bash
mkdir -p $REPO/data/faiss_index $REPO/data/snapshots $REPO/data/backups
docker compose -f docker/compose.yml config
```

Erwartet: gerendertes YAML ohne Fehler, kein `version:`-Feld, `network_mode: host` sichtbar, kein `ollama`-Service im Output.

- [ ] **Step 4: Commit**

```bash
git add docker/compose.yml docker/.env.example
git commit -m "chore: Docker Compose für Backend anlegen (natives Ollama statt eigenem Container)"
```

---

### Task 5: Natives Ollama verifizieren (ROCm + GPU-Zugriff)

**Files:**
- Keine neuen Dateien — reine Host-Verifikation, kein Container wird gestartet

**Interfaces:**
- Consumes: nichts
- Produces: bestätigt funktionsfähiges natives Ollama mit GPU-Zugriff auf `127.0.0.1:11434`, das Task 6 (Modelle pullen) und Task 9 (Benchmark) voraussetzen

Kein Container-Start in diesem Task — das native Ollama läuft bereits als User-systemd-Service. Dieser Task verifiziert nur, dass es funktioniert.

- [ ] **Step 1: ROCm auf dem Host prüfen**

```bash
rocm-smi
```

Erwartet: RX 6900 XT wird in der Ausgabe gelistet (GPU-ID, VRAM-/GPU-Auslastung).

- [ ] **Step 2: systemd-Status des nativen Ollama-Service prüfen**

```bash
systemctl --user status ollama
```

Erwartet: `Active: active (running)`.

- [ ] **Step 3: Ollama-API erreichbar und GPU-Umgebung korrekt verifizieren**

```bash
curl -s http://127.0.0.1:11434/api/tags | head -c 300
tr '\0' '\n' < /proc/"$(pgrep -f 'ollama serve')"/environ | grep -E 'HSA_OVERRIDE_GFX_VERSION|OLLAMA_MODELS'
```

Erwartet: JSON-Modell-Liste als Antwort; `HSA_OVERRIDE_GFX_VERSION=10.3.0` und ein `OLLAMA_MODELS`-Pfad erscheinen in der Umgebung.

- [ ] **Step 4: Ergebnis dokumentieren**

Kein Commit nötig (kein Code geändert) — bei Erfolg direkt zu Task 6.

---

### Task 6: Ollama-Modelle pullen

**Files:**
- Keine neuen Dateien — Host-Operation gegen das native Ollama

**Interfaces:**
- Consumes: laufendes natives Ollama (Task 5)
- Produces: lokal verfügbare Modelle `qwen3:4b`, `qwen3:8b`, `qwen3:14b`, `bge-m3`, die Task 9 (Benchmark) und später Phase 2 nutzen

- [ ] **Step 1: Alle vier Modelle pullen**

```bash
ollama pull qwen3:4b
ollama pull qwen3:8b
ollama pull qwen3:14b
ollama pull bge-m3
```

- [ ] **Step 2: Verifizieren, dass alle vier Modelle gelistet werden**

```bash
ollama list
```

Erwartet: `qwen3:4b`, `qwen3:8b`, `qwen3:14b`, `bge-m3` erscheinen jeweils mit Größe (neben den bereits vorhandenen Modellen).

- [ ] **Step 3: Verifizieren, dass genug Platz auf der Modell-Platte vorhanden ist**

```bash
tr '\0' '\n' < /proc/"$(pgrep -f 'ollama serve')"/environ | grep OLLAMA_MODELS
df -h /mnt/external
```

Erwartet: ausreichend freier Speicher für die zusätzlichen ~30–40 GB der vier Modelle.

---

### Task 7: Backup-Skript + Cron

**Files:**
- Create: `$REPO/data/backup.sh`

**Interfaces:**
- Consumes: `$REPO/data/cbks.db` (existiert erst ab Phase 2 — Skript muss idempotent mit „Datei fehlt noch" umgehen können, siehe Step 1)
- Produces: tägliches Backup-Verzeichnis unter `/mnt/external/cbks_backups/<DATUM>/`, dessen Restore in Step 4 getestet wird

Backup-**Ziel** liegt bewusst auf der exFAT-Platte (`/mnt/external`), nicht neben der Live-DB auf `/home`: Backups sind Kopien mit unkritischem Locking-Bedarf (einmal schreiben, gelegentlich lesen) und sollen die knappen ~35 GB auf der NVMe nicht mit 14 Tagen Rotation aufbrauchen. Die Live-Datenbank selbst bleibt auf `$REPO/data` (btrfs).

- [ ] **Step 1: Skript schreiben (Pfade an dieses Setup angepasst)**

```bash
#!/usr/bin/env bash
# $REPO/data/backup.sh – läuft nächtlich um 02:30 via systemd-Timer (backup.timer)

set -euo pipefail
STAMP=$(date +%Y-%m-%d)
DEST="/mnt/external/cbks_backups/$STAMP"
mkdir -p "$DEST"

# 1. SQLite: konsistentes Online-Backup (kein Stoppen nötig)
sqlite3 $REPO/data/cbks.db ".backup '$DEST/cbks.db'"

# 2. FAISS-Index + Snapshots
rsync -a $REPO/data/faiss_index/ "$DEST/faiss_index/"
rsync -a $REPO/data/snapshots/ "$DEST/snapshots/"

# 3. Rotation: Backups älter als 14 Tage löschen
find /mnt/external/cbks_backups/ -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf {} +
```

> **Korrektur ggü. Spec §6.5, festgelegt am 2026-07-04 im finalen Review:** Die Spec-Vorlage sicherte `snapshots/` trotz gegenteiligem Kommentar nicht mit (nur `faiss_index/` wurde per rsync kopiert). Ergänzt, da ab Phase 2 dort Daten liegen werden. Zusätzlich `-mindepth 1` bei der Rotation ergänzt, damit das Backup-Wurzelverzeichnis selbst nie als Löschkandidat zählt.

- [ ] **Step 2: Ausführbar machen**

```bash
chmod +x $REPO/data/backup.sh
```

- [ ] **Step 3: Testlauf mit einer Dummy-Datenbank (Phase-2-Schema existiert noch nicht)**

```bash
mkdir -p $REPO/data/faiss_index
sqlite3 $REPO/data/cbks.db "CREATE TABLE IF NOT EXISTS probe (id INTEGER);"
$REPO/data/backup.sh
ls /mnt/external/cbks_backups/"$(date +%Y-%m-%d)"/
```

Erwartet: Verzeichnis enthält `cbks.db` und `faiss_index/`.

- [ ] **Step 4: Restore verifizieren**

```bash
STAMP=$(date +%Y-%m-%d)
sqlite3 "/mnt/external/cbks_backups/$STAMP/cbks.db" ".tables"
```

Erwartet: `probe` erscheint — das Backup ist ein gültiges, lesbares SQLite-File.

- [ ] **Step 5: Zeitgesteuerten Lauf einrichten**

> **Abweichung, festgelegt am 2026-07-04:** Auf diesem Garuda/Arch-Host ist kein `cron`/`cronie` installiert. Statt eines zusätzlichen Daemons (Nutzerentscheidung) läuft der nächtliche Backup-Lauf über einen systemd-`--user`-Timer — passend zum bereits bestehenden Muster auf diesem Host (`ollama.service` läuft ebenfalls als User-Service). Logs landen in `journalctl --user -u backup.service` statt in `backup.log`.

```bash
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/backup.service <<'EOF'
[Unit]
Description=CBKS nächtliches Backup

[Service]
Type=oneshot
ExecStart=$REPO/data/backup.sh
EOF

cat > ~/.config/systemd/user/backup.timer <<'EOF'
[Unit]
Description=CBKS Backup täglich um 02:30 Uhr

[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now backup.timer
systemctl --user list-timers backup.timer
```

Erwartet: `backup.timer` erscheint in `systemctl --user list-timers` mit dem nächsten Trigger um 02:30 Uhr.

---

### Task 8: ENTFÄLLT — Docker-Root-Verlagerung

Gestrichen per Nutzerentscheidung am 2026-07-04: Die einzigen großen Laufwerke auf diesem Host sind exFAT-formatiert und damit als Docker-Root ungeeignet (Storage-Driver braucht ein natives Unix-Dateisystem). Docker-Root bleibt auf dem Standardpfad `/var/lib/docker` (NVMe). Kein Handlungsbedarf, kein Task-Review nötig — direkt zu Task 9.

---

### Task 9: Modell-Benchmark (4B / 8B / 14B)

**Files:**
- Create: `backend/tests/fixtures/benchmark_events.json`
- Create: `scripts/benchmark_models.py`
- Create: `docs/benchmark_results.md`

**Interfaces:**
- Consumes: natives Ollama mit `qwen3:4b/8b/14b` (Task 6), erreichbar auf `http://127.0.0.1:11434` — das `ollama`-Python-Paket verwendet diesen Endpunkt standardmäßig, das Skript läuft direkt auf dem Host, kein Container nötig
- Produces: `docs/benchmark_results.md` mit der finalen Modellentscheidung, die Spec §3.4 verlangt und die spätere Phase-2-Konfiguration (`QWEN_MODEL=qwen3:Xb`) referenziert

- [ ] **Step 1: Fixture mit den 5 Beispiel-Events gemäß Spec §3.4 anlegen**

```json
[
  {
    "event_type": "document.added",
    "label": "PDF",
    "text": "Titel: Grundlagen der Graphentheorie. Abschnitt 3.2 behandelt gerichtete azyklische Graphen (DAGs) und deren Anwendung in Abhängigkeitsauflösung. Autor: J. Klein, veröffentlicht 2024 im Rahmen der Vorlesung Diskrete Strukturen an der TU Berlin."
  },
  {
    "event_type": "commit.ingested",
    "label": "Git-Commit",
    "text": "commit 4f2a9c1\nAuthor: Al Ain\nfix(faiss): remove_ids schlägt bei leerem Index fehl\n\nIndexIDMap.remove_ids() wirft AssertionError, wenn der Index noch keine Vektoren enthält. Guard-Klausel ergänzt, die den Aufruf überspringt, falls ntotal == 0."
  },
  {
    "event_type": "note.created",
    "label": "Notiz",
    "text": "Idee für später: Emotionale Gewichtung von Notizen automatisch aus Wortwahl schätzen, bevor der Nutzer sie manuell setzen muss. Ggf. german-sentiment-bert dafür wiederverwenden, das ohnehin für Phase 3 geplant ist."
  },
  {
    "event_type": "document.added",
    "label": "Webseite",
    "text": "Ollama Blog: 'Running multiple models concurrently'. OLLAMA_MAX_LOADED_MODELS steuert, wie viele Modelle gleichzeitig im VRAM gehalten werden. Bei knappem VRAM empfiehlt sich ein Wert von 1, bei ausreichend Reserve (>12 GB) sind 2-3 Modelle parallel möglich."
  },
  {
    "event_type": "screenshot.added",
    "label": "Screenshot-Beschreibung",
    "text": "Screenshot eines VS-Code-Fensters: Terminal zeigt 'pytest tests/ -v', 8 Tests bestanden, 1 übersprungen ('test_gpu_only: no ROCm device'). Linkes Panel zeigt geöffnete Datei event_log.py mit der Funktion append()."
  }
]
```

- [ ] **Step 2: Benchmark-Skript schreiben**

```python
#!/usr/bin/env python3
"""Vergleicht qwen3:4b/8b/14b anhand der 5 Beispiel-Events aus Spec §3.4."""
import json
import time
import sys
from pathlib import Path

import ollama

MODELS = ["qwen3:4b", "qwen3:8b", "qwen3:14b"]
FIXTURE_PATH = Path(__file__).parent.parent / "backend/tests/fixtures/benchmark_events.json"

PROMPT_TEMPLATE = """Extrahiere aus folgendem Text:
1. Bis zu 5 benannte Entitäten (Konzepte, Personen, Technologien).
2. Eine Klassifikation des Event-Typs: eines von [document, commit, note, task, screenshot].

Antworte ausschließlich als JSON: {{"entities": [...], "classification": "..."}}

Text:
{text}
"""


def run_model(model: str, events: list[dict]) -> list[dict]:
    results = []
    for event in events:
        prompt = PROMPT_TEMPLATE.format(text=event["text"])
        start = time.monotonic()
        response = ollama.generate(model=model, prompt=prompt)
        latency = time.monotonic() - start
        results.append({
            "label": event["label"],
            "latency_seconds": round(latency, 2),
            "raw_response": response["response"],
        })
    return results


def main() -> None:
    events = json.loads(FIXTURE_PATH.read_text())
    report: dict[str, list[dict]] = {}
    for model in MODELS:
        print(f"--- {model} ---", file=sys.stderr)
        report[model] = run_model(model, events)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Skript ausführen und Rohergebnis sichern**

```bash
mkdir -p scripts
pip install ollama==0.2.0
python3 scripts/benchmark_models.py | tee /tmp/cbks_benchmark_raw.json
```

Erwartet: Für jedes der 3 Modelle und 5 Events ein JSON-Objekt mit `latency_seconds` und `raw_response`. Kein Python-Traceback.

- [ ] **Step 4: Ergebnisse manuell bewerten und Entscheidung dokumentieren**

`docs/benchmark_results.md` von Hand aus `/tmp/cbks_benchmark_raw.json` befüllen:

```markdown
# Modell-Benchmark: Qwen3 4B vs. 8B vs. 14B

Datum: <Datum des Testlaufs>

## Messkriterien (Spec §3.4)
- Korrektheit der Entitäten-Extraktion (manuell bewertet, 1–5 pro Event)
- Klassifikationsgenauigkeit (richtig/falsch pro Event)
- Latenz pro Event (Sekunden, aus Skript-Output)

## Ergebnistabelle

| Modell | Ø Latenz | Klassifikation korrekt (von 5) | Entitäten-Qualität (Ø 1–5) |
|---|---|---|---|
| qwen3:4b | ... | ... | ... |
| qwen3:8b | ... | ... | ... |
| qwen3:14b | ... | ... | ... |

## Entscheidung

Gewähltes Modell: `qwen3:__b`

Begründung: <kleinstes Modell, das die Messkriterien erfüllt — Spec-Regel "kleinstes ausreichendes Modell gewinnt">
```

- [ ] **Step 5: Ungenutzte Modelle optional entfernen, um VRAM/Platte freizuhalten**

```bash
# Nur ausführen, nachdem die Entscheidung in docs/benchmark_results.md steht:
# ollama rm qwen3:4b   # Beispiel, falls 8B gewinnt
# ollama rm qwen3:14b
```

- [ ] **Step 6: Commit**

```bash
git add backend/tests/fixtures/benchmark_events.json scripts/benchmark_models.py docs/benchmark_results.md
git commit -m "docs: Modell-Benchmark durchgeführt, Qwen3-Größe gemäß Spec §3.4 entschieden"
```

---

## Abschluss-Kriterium für Phase 1

Angepasst an die reale Hardware (siehe Abweichungs-Hinweis im Header):

1. `rocm-smi` erkennt die GPU (Task 5)
2. Natives Ollama läuft mit GPU-Zugriff, erreichbar auf `127.0.0.1:11434` (Task 5)
3. Alle vier Modelle sind über das native Ollama verfügbar (Task 6)
4. Modellgröße ist final entschieden und dokumentiert (Task 9)
5. ~~`df -h` zeigt > 100 GB frei auf der SSD~~ — entfällt (Task 8 gestrichen, kein Docker-Root-Umzug)
6. Erstes Backup existiert, Restore wurde getestet (Task 7)

Damit ist die Grundlage für Phase 2 (MVP-Kern mit CLI, Event-Log, GraphBackend) gelegt.
