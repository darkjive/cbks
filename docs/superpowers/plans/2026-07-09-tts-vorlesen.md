# Notizen vorlesen (TTS) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Beim Anklicken eines Knotens wird `node.content` automatisch mit einer deutschen, JARVIS-artigen Stimme (Kokoro-German "Martin" + ffmpeg-Effektkette) vorgelesen — lokal, kostenlos, mit Datei-Cache. Auto-Play ist global abschaltbar; ein manueller Play/Stop-Button existiert zusätzlich.

**Architecture:** Neuer Backend-Service `backend/services/tts.py` kapselt Kokoro-Pipeline-Loading (lazy Singleton, thread-sicher) und Synthese+Cache+ffmpeg-Effekt hinter einer einzigen Funktion `synthesize(text, cache_dir) -> Path`. Ein neuer Endpoint `GET /nodes/{node_id}/audio` liefert die WAV-Datei aus. Das Frontend holt sie per neuem `apiFetchBlob`-Helper (Audio-Endpoints können keinen `X-API-Key`-Header über `<audio src>` mitschicken), spielt sie über ein `HTMLAudioElement` ab und steuert Auto-Play/manuellen Button über zwei neue State-Variablen in `App.tsx`.

**Tech Stack:** Backend: FastAPI, Kokoro-German (PyTorch/CPU), ffmpeg (Subprozess), pytest (Service- und API-Tests, echte Synthese wird gemockt). Frontend: React 19, TypeScript, kein Test-Runner (Verifikation via `npm run build` + manueller Browser-/Hörtest).

## Global Constraints

- Alles läuft lokal, kostenlos, ohne Cloud-API (Apache-2.0-Modelle) — local-first bleibt local-first.
- Auto-Play global abschaltbar (Toggle in `StatsBar`, `localStorage`-Key `cbks-tts-enabled`, Default **true**).
- Nur `node.content` wird vorgelesen (ohne Titel). Leerer Inhalt → Backend `422`, Frontend ignoriert das still (kein Toast) im Auto-Play-Fall; der Play-Button wird für solche Knoten gar nicht gerendert.
- Kein Streaming, kein Fortschrittsbalken, kein Geschwindigkeitsregler, keine weiteren Stimmen/Konfigurierbarkeit — alles Nicht-in-Scope laut Spec.
- Kein Vorlesen von Titel, Metriken oder Suchergebnissen.
- Docker-Image-Anpassung (echtes `apt-get install espeak-ng ffmpeg` im Dockerfile) ist **nicht** Teil dieser Aufgabe — nur ein Hinweis-Kommentar.
- Auth läuft automatisch über die bestehende `require_api_key`-Dependency (app-weit in `backend/main.py:38`) — kein neuer Auth-Code nötig.
- Kein Modell-Download in CI/Tests: automatisierte Tests mocken die Synthese vollständig (Kokoro/ffmpeg werden nie wirklich aufgerufen). Der echte Kokoro-Pfad wird einmalig manuell verifiziert (Hörprobe), bevor Task 2/3 beginnen.
- Referenz-Design: `docs/superpowers/specs/2026-07-09-tts-vorlesen-design.md`.
- System-Voraussetzungen `espeak-ng` und `ffmpeg` sind auf diesem Host bereits vorhanden (verifiziert: `/usr/bin/espeak-ng`, `/usr/bin/ffmpeg`).
- Das Projekt-`.venv` (`$REPO/.venv`) ist die Python-Umgebung für alle Backend-Befehle in diesem Plan (`.venv/bin/python`, `.venv/bin/pip`, `.venv/bin/pytest`).

---

## Task 1: Backend — `backend/services/tts.py` (Kokoro-Synthese, Cache, ffmpeg-Effekt)

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/Dockerfile`
- Create: `backend/services/tts.py`
- Create: `backend/tests/test_tts.py`

**Interfaces:**
- Produces: `synthesize(text: str, cache_dir: Path) -> Path` in `backend/services/tts.py` — Task 2 ruft das als `tts_service.synthesize(text, ctx.config.data_dir)` auf.
- Internal (für Tests mockbar, kein externer Konsument): `_generate_audio(text: str) -> numpy.ndarray`, `_load_pipeline() -> tuple[Any, str]`.

- [ ] **Step 1: Abhängigkeiten ergänzen und installieren**

Füge am Anfang von `backend/requirements.txt` (vor der ersten Zeile) ein:

```
--extra-index-url https://download.pytorch.org/whl/cpu
```

Füge am Ende von `backend/requirements.txt` an:

```

# TTS – Kokoro-German-Fork (Paketname "kokoro" 0.9.4, ergänzt lang_code 'd' via espeak)
kokoro @ git+https://github.com/Thomcle/kokoro_german@81b2747c15a7f0f6092b3efb1971d91e2b498467
soundfile==0.13.1
huggingface_hub==0.36.2
```

Installiere:

```bash
.venv/bin/pip install -r backend/requirements.txt
```

Erwartet: Installation läuft durch (kann einige Minuten dauern, `torch` ist bereits vorhanden). Falls ein Fehler auftritt, der nicht mit den TTS-Paketen zusammenhängt (z.B. ein bereits vorher kaputtes Paket), melde das als BLOCKED statt es zu "reparieren".

- [ ] **Step 2: Docker-Hinweis-Kommentar ergänzen (keine echte Änderung)**

In `backend/Dockerfile`, ersetze:

```dockerfile
# Nur Build-Werkzeuge – KEIN ROCm nötig:
# Das Backend spricht mit Ollama nur per HTTP, GPU-Zugriff hat allein das native Ollama auf dem Host.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*
```

durch:

```dockerfile
# Nur Build-Werkzeuge – KEIN ROCm nötig:
# Das Backend spricht mit Ollama nur per HTTP, GPU-Zugriff hat allein das native Ollama auf dem Host.
# TTS-Hinweis: espeak-ng und ffmpeg werden vom TTS-Feature (backend/services/tts.py) zur
# Laufzeit gebraucht (System-Binaries, kein pip-Paket) und fehlen hier noch — wer dieses
# Dockerfile produktiv für TTS nutzen will, muss "espeak-ng ffmpeg" zur apt-get-Zeile unten
# ergänzen. Nicht Teil dieser Aufgabe (YAGNI, solange das Backend lokal ohne Docker läuft).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 3: Fehlschlagenden Cache-Test schreiben (TDD RED)**

Erstelle `backend/tests/test_tts.py`:

```python
from pathlib import Path

import numpy as np

from backend.services import tts


def test_synthesize_caches_and_skips_regeneration(tmp_path, monkeypatch):
    calls = []

    def fake_generate_audio(text: str):
        calls.append(text)
        return np.zeros(100, dtype="float32")

    def fake_ffmpeg_run(cmd, **kwargs):
        target = Path(cmd[-1])
        target.write_bytes(b"RIFF-fake-wav-bytes")
        return None

    monkeypatch.setattr(tts, "_generate_audio", fake_generate_audio)
    monkeypatch.setattr(tts.subprocess, "run", fake_ffmpeg_run)

    path1 = tts.synthesize("Hallo Welt", tmp_path)
    assert path1.exists()
    assert path1.read_bytes() == b"RIFF-fake-wav-bytes"
    assert calls == ["Hallo Welt"]

    path2 = tts.synthesize("Hallo Welt", tmp_path)
    assert path2 == path1
    assert calls == ["Hallo Welt"]  # zweiter Aufruf generiert NICHT erneut


def test_synthesize_uses_distinct_cache_files_per_text(tmp_path, monkeypatch):
    def fake_generate_audio(text: str):
        return np.zeros(10, dtype="float32")

    def fake_ffmpeg_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"fake")
        return None

    monkeypatch.setattr(tts, "_generate_audio", fake_generate_audio)
    monkeypatch.setattr(tts.subprocess, "run", fake_ffmpeg_run)

    path_a = tts.synthesize("Text A", tmp_path)
    path_b = tts.synthesize("Text B", tmp_path)

    assert path_a != path_b
```

- [ ] **Step 4: Test laufen lassen, RED verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_tts.py -v`
Expected: FAIL — `ModuleNotFoundError` oder `AttributeError`, da `backend/services/tts.py` noch nicht existiert.

- [ ] **Step 5: `backend/services/tts.py` implementieren**

Erstelle `backend/services/tts.py`:

```python
from __future__ import annotations

import hashlib
import subprocess
import threading
from pathlib import Path
from typing import Any

MODEL_REPO = "kikiri-tts/kikiri-german-martin"   # Apache-2.0
MODEL_FILE = "kikiri_german_martin_ep10.pth"      # Kokoro-kompatible Gewichte (~327 MB)
VOICE_FILE = "voices/martin.pt"                   # Style-Embedding (~525 KB)
CONFIG_REPO = "hexgrad/Kokoro-82M"                # kikiri-Repo hat KEINE config.json,
CONFIG_FILE = "config.json"                       # Architektur/Vocab sind identisch
CACHE_TAG = "martin-jarvis-v1"                    # bei Stimm-/Effektänderung hochzählen
FFMPEG_FILTER = (
    "highpass=f=120,lowpass=f=7500,"
    "equalizer=f=2500:t=q:w=1.5:g=3,"
    "chorus=0.6:0.9:50:0.3:0.25:2,"
    "aecho=0.6:0.4:12:0.18,"
    "alimiter=level_in=1.2"
)
SPLIT_PATTERN = r"(?<=[.!?:;])\s+"  # espeak-G2P kürzt sonst lange Absätze ohne Zeilenumbruch

_pipeline: Any = None
_voice_path: str = ""
_pipeline_lock = threading.Lock()


def _load_pipeline() -> tuple[Any, str]:
    # Lazy Singleton hinter Lock: FastAPI-sync-Endpoints laufen im Threadpool,
    # das Modell darf nur einmal pro Prozess geladen werden.
    global _pipeline, _voice_path
    if _pipeline is None:
        with _pipeline_lock:
            if _pipeline is None:
                from huggingface_hub import hf_hub_download
                from kokoro import KModel, KPipeline

                config_path = hf_hub_download(repo_id=CONFIG_REPO, filename=CONFIG_FILE)
                model_path = hf_hub_download(repo_id=MODEL_REPO, filename=MODEL_FILE)
                voice_path = hf_hub_download(repo_id=MODEL_REPO, filename=VOICE_FILE)

                kmodel = KModel(repo_id=MODEL_REPO, config=config_path, model=model_path).eval()
                _pipeline = KPipeline(lang_code="d", model=kmodel, repo_id=MODEL_REPO, device="cpu")
                _voice_path = voice_path
    return _pipeline, _voice_path


def _generate_audio(text: str):
    # Eigene Funktion (statt inline in synthesize), damit Tests die reale
    # Kokoro-Synthese mocken koennen, ohne _load_pipeline (Modell-Download) anzufassen.
    import numpy as np

    pipeline, voice_path = _load_pipeline()
    chunks = [
        result.audio.numpy()
        for result in pipeline(text, voice=voice_path, speed=1, split_pattern=SPLIT_PATTERN)
    ]
    return np.concatenate(chunks)


def synthesize(text: str, cache_dir: Path) -> Path:
    key = hashlib.sha256(f"{CACHE_TAG}:{text}".encode()).hexdigest()
    tts_cache_dir = cache_dir / "tts_cache"
    tts_cache_dir.mkdir(parents=True, exist_ok=True)
    target_path = tts_cache_dir / f"{key}.wav"
    if target_path.exists():
        return target_path

    import soundfile as sf

    audio = _generate_audio(text)
    raw_path = tts_cache_dir / f"{key}.raw.wav"
    sf.write(raw_path, audio, 24000)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(raw_path), "-af", FFMPEG_FILTER, str(target_path)],
            check=True, capture_output=True,
        )
    finally:
        raw_path.unlink(missing_ok=True)

    return target_path
```

**Wichtiger Hinweis zur Kokoro-API in `_generate_audio`:** Die Zeile `result.audio.numpy()` geht davon aus, dass `pipeline(text, ...)` beim Iterieren Objekte mit einem `.audio`-Attribut (`torch.FloatTensor`) liefert (aktuelle Kokoro-API, Version 0.9.x). Das wird in Step 7 (Smoke-Test) gegen das tatsächlich installierte Paket verifiziert. Falls die installierte Version stattdessen ein 3-Tupel `(graphemes, phonemes, audio)` liefert (ältere Kokoro-Konvention), ändere die Zeile zu:

```python
    chunks = [audio.numpy() for _, _, audio in pipeline(text, voice=voice_path, speed=1, split_pattern=SPLIT_PATTERN)]
```

Nimm erst nach Step 7 die endgültige Variante.

- [ ] **Step 6: Test laufen lassen, GREEN verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_tts.py -v`
Expected: `2 passed`. Diese Tests rufen `_generate_audio` gemockt auf und laden nie echtes Kokoro/torch — falls sie fehlschlagen, liegt es an der Cache-/Dateilogik in `synthesize`, nicht an Kokoro.

- [ ] **Step 7: Manueller Smoke-Test der echten Kokoro-Pipeline (Hörprobe)**

Dies ist der riskanteste Teil der Aufgabe (externes ML-Paket, Voicepack-Kompatibilität) — laut Spec-Risikoabschnitt bewusst vor der restlichen Arbeit (Task 2/3) zu verifizieren. Führe direkt im Python-Interpreter aus (erster Aufruf lädt ~330 MB Modellgewichte herunter, kann mehrere Minuten dauern):

```bash
.venv/bin/python -c "
from pathlib import Path
from backend.services import tts
path = tts.synthesize('Hallo, ich bin CBKS. Dies ist ein Test der Sprachausgabe.', Path('/tmp/cbks-tts-smoke'))
print('Erzeugt:', path, path.stat().st_size, 'Bytes')
"
```

Erwartet: Datei wird erzeugt, Größe > 0. Falls ein `AttributeError` auf `.audio` auftritt (oder eine andere Struktur-Fehlermeldung beim Iterieren von `pipeline(...)`), wende die in Step 5 genannte Tupel-Variante an und wiederhole diesen Schritt.

Spiele die erzeugte Datei ab und höre rein (z.B. `paplay /tmp/cbks-tts-smoke/tts_cache/*.wav` oder die Datei per Dateimanager öffnen):
- Ist es verständliches Deutsch?
- Klingt die Stimme männlich/ruhig mit einem leicht "technischen" Klangcharakter (Chorus/Echo-Effekt hörbar, aber nicht übertrieben)?

Wenn die Stimme kaputt/falsch klingt (z.B. Rauschen, falsche Sprache, Roboter-Stottern jenseits des gewollten Effekts) → BLOCKED, nicht selbst an Stimm-Dateien herumraten (siehe Spec-Risikoabschnitt: Fallback wäre Vendoring, das ist außerhalb dieser Aufgabe).

- [ ] **Step 8: Cache-Tests erneut laufen lassen (falls Step 7 Anpassungen brauchte)**

Run: `.venv/bin/python -m pytest backend/tests/test_tts.py -v`
Expected: weiterhin `2 passed`.

- [ ] **Step 9: Commit**

```bash
git add backend/requirements.txt backend/Dockerfile backend/services/tts.py backend/tests/test_tts.py
git commit -m "feat: Kokoro-TTS-Service mit Datei-Cache und JARVIS-Effekt"
```

---

## Task 2: Backend — `GET /nodes/{node_id}/audio` Endpoint

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `tts.synthesize(text: str, cache_dir: Path) -> Path` aus `backend/services/tts.py` (Task 1).
- Consumes: `ctx.config.data_dir: Path` (bestehend, `backend/config.py:13`), `ctx.graph.get_node(node_id) -> Optional[Node]` (bestehend).
- Produces: Route `GET /nodes/{node_id}/audio` — kein weiterer Task konsumiert das direkt (Task 3/Frontend ruft es über die feste URL auf, nicht über ein Python-Interface).

- [ ] **Step 1: Fehlschlagende Endpoint-Tests schreiben (TDD RED)**

In `backend/tests/test_api.py`, ergänze die Imports am Dateianfang. Ersetze:

```python
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.agents.prefrontal import OllamaLLMClient
from backend.services.agents.temporal import OllamaEmbeddingClient

client = TestClient(app)
```

durch:

```python
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app_context import build_context
from backend.main import app
from backend.models.nodes import Node
from backend.services.agents.prefrontal import OllamaLLMClient
from backend.services.agents.temporal import OllamaEmbeddingClient

client = TestClient(app)
```

Füge am Ende von `backend/tests/test_api.py` an:

```python


def test_get_node_audio_returns_wav(monkeypatch):
    def fake_synthesize(text, cache_dir):
        path = cache_dir / "fake.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"RIFF-fake-wav-bytes")
        return path

    monkeypatch.setattr("backend.main.tts_service.synthesize", fake_synthesize)

    client.post("/notes", json={"text": "Ein Text ueber Graphentheorie"})
    node_id = client.get("/search", params={"q": "Graphentheorie"}).json()[0]["node"]["id"]

    response = client.get(f"/nodes/{node_id}/audio")

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == b"RIFF-fake-wav-bytes"


def test_get_node_audio_unknown_node_returns_404():
    response = client.get("/nodes/does-not-exist/audio")

    assert response.status_code == 404


def test_get_node_audio_empty_content_returns_422():
    ctx = build_context()
    ctx.graph.add_node(Node(
        id="empty-content-node", title="Leer", type="note",
        creation_time="2026-07-09T00:00:00+00:00", last_access="2026-07-09T00:00:00+00:00",
    ))

    response = client.get("/nodes/empty-content-node/audio")

    assert response.status_code == 422
```

- [ ] **Step 2: Tests laufen lassen, RED verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_api.py -k audio -v`
Expected: FAIL — `404 Not Found` für alle drei (Route existiert noch nicht).

- [ ] **Step 3: Endpoint implementieren**

In `backend/main.py`, ersetze die Imports:

```python
import asyncio
import hashlib
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
```

durch:

```python
import asyncio
import hashlib
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
```

Ersetze:

```python
from backend.services.agents import pineal
from backend.services.ingestion import ingest_file, ingest_note
```

durch:

```python
from backend.services import tts as tts_service
from backend.services.agents import pineal
from backend.services.ingestion import ingest_file, ingest_note
```

Ersetze:

```python
@app.get("/nodes/{node_id}", response_model=NodeResponse)
def get_node(node_id: str) -> NodeResponse:
    ctx = build_context()
    node = ctx.graph.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node nicht gefunden")
    neighbors = ctx.graph.get_neighbors(node_id)
    return NodeResponse(node=node, neighbors=neighbors)
```

durch:

```python
@app.get("/nodes/{node_id}", response_model=NodeResponse)
def get_node(node_id: str) -> NodeResponse:
    ctx = build_context()
    node = ctx.graph.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node nicht gefunden")
    neighbors = ctx.graph.get_neighbors(node_id)
    return NodeResponse(node=node, neighbors=neighbors)


@app.get("/nodes/{node_id}/audio")
def get_node_audio(node_id: str) -> FileResponse:
    ctx = build_context()
    node = ctx.graph.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node nicht gefunden")
    text = (node.content or "").strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Node hat keinen vorlesbaren Inhalt")
    wav_path = tts_service.synthesize(text, ctx.config.data_dir)
    return FileResponse(wav_path, media_type="audio/wav")
```

- [ ] **Step 4: Tests laufen lassen, GREEN verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_api.py -k audio -v`
Expected: `3 passed`.

- [ ] **Step 5: Gesamte Backend-Suite laufen lassen**

Run: `.venv/bin/python -m pytest backend/tests/ -q`
Expected: alle Tests grün (vorher 132 bestehende + 2 neue aus Task 1 + 3 neue aus Task 2 = 137).

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/tests/test_api.py
git commit -m "feat: GET /nodes/{id}/audio Endpoint fuer TTS-Vorlesen"
```

---

## Task 3: Frontend — Wiedergabe-Logik, Auto-Play-Toggle, Play/Stop-Button

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/StatsBar.tsx`
- Modify: `frontend/src/components/NodeDetailPanel.tsx`
- Modify: `frontend/src/styles/global.css`

**Interfaces:**
- Consumes: `GET /nodes/{node_id}/audio` (Task 2), liefert `audio/wav` oder `404`/`422`.
- Consumes: `ApiError` Klasse aus `frontend/src/api/client.ts` (bestehend) — `err.status` wird auf `422` geprüft.
- Produces: nichts, das andere zukünftige Tasks konsumieren (letzter Task des Features).

Alle vier Datei-Änderungen gehören in diesen einen Task, weil TypeScript (`noUnusedLocals: true` in `frontend/tsconfig.app.json:19`) und die JSX-Prop-Typprüfung sonst bei jedem Zwischen-Commit fehlschlagen würden: `App.tsx` deklariert neue State-Variablen/Funktionen, die erst durch die neuen Props in `StatsBar`/`NodeDetailPanel` tatsächlich verwendet werden.

- [ ] **Step 1: `apiFetchBlob`-Helper ergänzen**

In `frontend/src/api/client.ts`, füge am Ende der Datei an:

```typescript

export async function apiFetchBlob(path: string): Promise<Blob> {
  const headers = new Headers();
  if (apiKey) {
    headers.set("X-API-Key", apiKey);
  }

  const response = await fetch(path, { headers });

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // kein JSON-Body vorhanden
    }
    throw new ApiError(response.status, body);
  }

  return await response.blob();
}
```

- [ ] **Step 2: Wiedergabe-State und -Logik in `App.tsx` ergänzen**

Ersetze den Import-Block:

```tsx
import { apiFetch } from "./api/client";
import type { GraphResponse, Node, Edge, NodeDetailResponse, SearchHit } from "./api/types";
```

durch:

```tsx
import { ApiError, apiFetch, apiFetchBlob } from "./api/client";
import type { GraphResponse, Node, Edge, NodeDetailResponse, SearchHit } from "./api/types";

const TTS_STORAGE_KEY = "cbks-tts-enabled";
type AudioState = "idle" | "loading" | "playing";
```

Ersetze:

```tsx
  const [refreshKey, setRefreshKey] = useState(0);
  const [view, setView] = useState<"graph" | "analysis" | "chat">("graph");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
```

durch:

```tsx
  const [refreshKey, setRefreshKey] = useState(0);
  const [view, setView] = useState<"graph" | "analysis" | "chat">("graph");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [ttsEnabled, setTtsEnabled] = useState<boolean>(() => {
    const stored = localStorage.getItem(TTS_STORAGE_KEY);
    return stored === null ? true : stored === "true";
  });
  const [audioState, setAudioState] = useState<AudioState>("idle");
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioObjectUrlRef = useRef<string | null>(null);
  const playRequestRef = useRef(0);
```

`useRef` muss zum React-Import ergänzt werden. Ersetze:

```tsx
import { useEffect, useState, useCallback } from "react";
```

durch:

```tsx
import { useEffect, useState, useCallback, useRef } from "react";
```

Ersetze:

```tsx
  const expandTo = useCallback((sectionId: string) => {
    setSidebarCollapsed(false);
    requestAnimationFrame(() => {
      document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, []);
```

durch:

```tsx
  const expandTo = useCallback((sectionId: string) => {
    setSidebarCollapsed(false);
    requestAnimationFrame(() => {
      document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, []);

  const toggleTts = useCallback(() => {
    setTtsEnabled((prev) => {
      const next = !prev;
      localStorage.setItem(TTS_STORAGE_KEY, String(next));
      return next;
    });
  }, []);

  const stopAudio = useCallback(() => {
    audioRef.current?.pause();
    if (audioObjectUrlRef.current) {
      URL.revokeObjectURL(audioObjectUrlRef.current);
      audioObjectUrlRef.current = null;
    }
    audioRef.current = null;
    setAudioState("idle");
  }, []);

  const playNode = useCallback(
    async (id: string) => {
      stopAudio();
      const requestId = ++playRequestRef.current;
      setAudioState("loading");
      try {
        const blob = await apiFetchBlob(`/nodes/${id}/audio`);
        if (playRequestRef.current !== requestId) return;
        const url = URL.createObjectURL(blob);
        audioObjectUrlRef.current = url;
        const audio = new Audio(url);
        audioRef.current = audio;
        audio.onended = () => {
          if (playRequestRef.current === requestId) setAudioState("idle");
        };
        audio.onerror = () => {
          if (playRequestRef.current === requestId) setAudioState("idle");
        };
        await audio.play();
        if (playRequestRef.current === requestId) setAudioState("playing");
      } catch (err) {
        if (playRequestRef.current !== requestId) return;
        setAudioState("idle");
        if (err instanceof ApiError && err.status === 422) return;
        pushError(err, "Notiz konnte nicht vorgelesen werden");
      }
    },
    [pushError, stopAudio]
  );

  useEffect(() => {
    const id = selectedNode?.node.id;
    const content = selectedNode?.node.content;
    if (id && ttsEnabled && content) {
      playNode(id);
    } else {
      stopAudio();
    }
    return () => {
      stopAudio();
    };
    // Absichtlich nur auf die Node-ID getriggert (nicht auf ttsEnabled/content/playNode/
    // stopAudio) - das Umschalten des Toggles soll die schon offene Notiz nicht
    // rueckwirkend stoppen/starten, nur zukuenftige Knoten-Klicks.
  }, [selectedNode?.node.id]);
```

- [ ] **Step 3: Toggle-Button und Play/Stop-Button verdrahten**

Ersetze:

```tsx
            <div className="sidebar-section" id="actions-section">
              <h2>Aktionen</h2>
              <StatsBar refreshKey={refreshKey} onGraphChanged={triggerRefresh} />
            </div>
```

durch:

```tsx
            <div className="sidebar-section" id="actions-section">
              <h2>Aktionen</h2>
              <StatsBar
                refreshKey={refreshKey}
                onGraphChanged={triggerRefresh}
                ttsEnabled={ttsEnabled}
                onToggleTts={toggleTts}
              />
            </div>
```

Ersetze:

```tsx
      <NodeDetailPanel
        detail={selectedNode}
        edges={edges}
        onClose={() => setSelectedNode(null)}
      />
```

durch:

```tsx
      <NodeDetailPanel
        detail={selectedNode}
        edges={edges}
        onClose={() => setSelectedNode(null)}
        audioState={audioState}
        onPlayAudio={() => selectedNode && playNode(selectedNode.node.id)}
        onStopAudio={stopAudio}
      />
```

- [ ] **Step 4: `StatsBar.tsx` — Toggle-Button ergänzen**

Ersetze:

```tsx
interface Props {
  refreshKey: number;
  onGraphChanged: () => void;
}
```

durch:

```tsx
interface Props {
  refreshKey: number;
  onGraphChanged: () => void;
  ttsEnabled: boolean;
  onToggleTts: () => void;
}
```

Ersetze:

```tsx
export function StatsBar({ refreshKey, onGraphChanged }: Props) {
```

durch:

```tsx
export function StatsBar({ refreshKey, onGraphChanged, ttsEnabled, onToggleTts }: Props) {
```

Ersetze:

```tsx
        <button
          className="btn-action"
          disabled={busy.backup}
          onClick={() => runAction("backup", "/backup", "Backup", false)}
        >
          {busy.backup ? "…" : "Backup"}
        </button>
      </div>
    </div>
  );
}
```

durch:

```tsx
        <button
          className="btn-action"
          disabled={busy.backup}
          onClick={() => runAction("backup", "/backup", "Backup", false)}
        >
          {busy.backup ? "…" : "Backup"}
        </button>
        <button className="btn-action" onClick={onToggleTts}>
          {ttsEnabled ? "🔊 Vorlesen an" : "🔇 Vorlesen aus"}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: `NodeDetailPanel.tsx` — Play/Stop-Button ergänzen**

Ersetze:

```tsx
interface Props {
  detail: NodeDetailResponse | null;
  edges: Edge[];
  onClose: () => void;
}
```

durch:

```tsx
interface Props {
  detail: NodeDetailResponse | null;
  edges: Edge[];
  onClose: () => void;
  audioState: "idle" | "loading" | "playing";
  onPlayAudio: () => void;
  onStopAudio: () => void;
}
```

Ersetze:

```tsx
export function NodeDetailPanel({ detail, edges, onClose }: Props) {
  if (!detail) return null;
  const { node, neighbors } = detail;

  const neighborMap = new Map(neighbors.map((n) => [n.id, n]));
  const myEdges = edges.filter(
    (e) => e.source === node.id || e.target === node.id
  );

  const emotionalColor = node.emotional_weight >= 0 ? "#6CE07A" : "#E06C8E";

  return (
    <aside className="node-detail-panel">
      <button type="button" onClick={onClose}>
        Schließen
      </button>

      <h2 className="detail-title">{node.title}</h2>
      <div
        className="node-type-tag"
        style={{ color: NODE_TYPE_COLORS[node.type] ?? "#ccc" }}
      >
        {"●"} {node.type}
      </div>
```

durch:

```tsx
export function NodeDetailPanel({
  detail, edges, onClose, audioState, onPlayAudio, onStopAudio,
}: Props) {
  if (!detail) return null;
  const { node, neighbors } = detail;

  const neighborMap = new Map(neighbors.map((n) => [n.id, n]));
  const myEdges = edges.filter(
    (e) => e.source === node.id || e.target === node.id
  );

  const emotionalColor = node.emotional_weight >= 0 ? "#6CE07A" : "#E06C8E";

  return (
    <aside className="node-detail-panel">
      <button type="button" onClick={onClose}>
        Schließen
      </button>

      <div className="detail-title-row">
        <h2 className="detail-title">{node.title}</h2>
        {node.content && (
          audioState === "loading" ? (
            <button type="button" className="audio-btn" disabled title="Wird geladen…">
              {"⏳"}
            </button>
          ) : audioState === "playing" ? (
            <button type="button" className="audio-btn" onClick={onStopAudio} title="Vorlesen stoppen">
              {"⏹"}
            </button>
          ) : (
            <button type="button" className="audio-btn" onClick={onPlayAudio} title="Vorlesen">
              {"▶"}
            </button>
          )
        )}
      </div>
      <div
        className="node-type-tag"
        style={{ color: NODE_TYPE_COLORS[node.type] ?? "#ccc" }}
      >
        {"●"} {node.type}
      </div>
```

- [ ] **Step 6: CSS für Titel-Zeile und Audio-Button ergänzen**

In `frontend/src/styles/global.css`, ersetze:

```css
.detail-title {
  font-size: 1.05rem;
  font-weight: 600;
  margin: 0;
  word-break: break-word;
}
```

durch:

```css
.detail-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.5rem;
}

.detail-title {
  font-size: 1.05rem;
  font-weight: 600;
  margin: 0;
  word-break: break-word;
}

.audio-btn {
  flex-shrink: 0;
  padding: 0.25rem 0.5rem;
  font-size: 0.9rem;
  line-height: 1;
}
```

- [ ] **Step 7: Build verifizieren**

Run: `cd frontend && npm run build`
Expected: `✓ built in ...` ohne TypeScript-Fehler (insbesondere keine `noUnusedLocals`-Fehler für `toggleTts`/`audioState`/etc.).

- [ ] **Step 8: Manueller Browser-Test (mit echtem Backend)**

Backend muss laufen (`.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000`, aus dem Repo-Root, `CBKS_DATA_DIR` gesetzt oder Default nutzen). Run: `cd frontend && npm run dev`, im Browser öffnen.

Prüfen:
- In der Sidebar unter "Aktionen" erscheint ein Button "🔊 Vorlesen an" (bzw. nach Klick "🔇 Vorlesen aus").
- Einen Knoten mit Inhalt anklicken: bei aktiviertem Toggle startet automatisch die Sprachausgabe (erste Synthese pro Text dauert spürbar länger — Modell-Ladezeit + Synthese; danach sofort dank Cache).
- Im Node-Detail-Panel erscheint neben dem Titel ein Button (▶ im Ruhezustand, ⏳ während des Ladens, ⏹ während der Wiedergabe). Klick auf ⏹ stoppt die Wiedergabe.
- Toggle ausschalten, Seite neu laden (`localStorage` bleibt erhalten), erneut einen Knoten anklicken: **kein** Auto-Play, aber der manuelle ▶-Button funktioniert trotzdem.
- Schnell zwischen zwei Knoten wechseln: keine überlappende/doppelte Wiedergabe (alte Wiedergabe stoppt sofort).
- Einen Knoten ohne Inhalt anklicken (falls vorhanden) oder `content: null`: kein Button im Detail-Panel, kein Fehler-Toast.

Dev-Server und Backend danach beenden.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/App.tsx frontend/src/components/StatsBar.tsx frontend/src/components/NodeDetailPanel.tsx frontend/src/styles/global.css
git commit -m "feat: Notizen vorlesen (Kokoro-TTS, Auto-Play-Toggle, Play/Stop-Button)"
```
