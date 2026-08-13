# Spec: Notizen vorlesen (TTS „JARVIS", deutsch)

**Datum:** 2026-07-09
**Status:** Entwurf (vom Nutzer freigegeben, Stimme final: Martin + KI-Effekt)

## Ziel

Beim Anklicken eines Knotens im Gehirn-Wiki wird der **Notiz-Inhalt** (ohne Titel)
automatisch vorgelesen — mit einer deutschen, JARVIS-artigen Stimme (ruhige männliche
Kokoro-Stimme „Martin" + dezenter KI-Audioeffekt). Auto-Play ist global abschaltbar.
Alles läuft lokal und kostenlos (Apache-2.0-Modelle, keine Cloud-API).

## Entscheidungen (mit Nutzer geklärt)

| Frage | Entscheidung |
|---|---|
| Engine | Kokoro-German (PyTorch, CPU) — **nicht** kokorottsai.com (kann kein Deutsch) |
| Stimme | `kikiri-tts/kikiri-german-martin` (lebendes HF-Repo, Apache-2.0). `df_eva` verworfen: deren Basismodell (Tundragoon) wurde von HF gelöscht |
| Klang | „JARVIS": dezente ffmpeg-Effektkette (Chorus + kurzes Echo + EQ), serverseitig eingebrannt |
| Auslöser | Auto-Play bei Knoten-Klick, global abschaltbar (Toggle in StatsBar, localStorage) |
| Inhalt | Nur `node.content`, ohne Titel. Leerer Inhalt → keine Wiedergabe |

## Architektur

```
Klick auf Knoten (GraphCanvas → App.tsx, selectedNode)
  └─ ttsEnabled? → fetch GET /nodes/{id}/audio  (X-API-Key-Header!)
       Backend: Cache-Hit? → WAV sofort
                Cache-Miss → Kokoro-Synthese (CPU) → ffmpeg-Effekt → Cache → WAV
  └─ Blob → URL.createObjectURL → HTMLAudioElement.play()
```

## Backend

### Neue Abhängigkeiten (`backend/requirements.txt`)

```
# TTS – Kokoro-German-Fork (Paketname "kokoro" 0.9.4, ergänzt lang_code 'd' via espeak)
kokoro @ git+https://github.com/Thomcle/kokoro_german@81b2747c15a7f0f6092b3efb1971d91e2b498467
soundfile==0.13.1
```

- `kokoro` zieht `torch` transitiv. **CPU-Wheel erzwingen** (AMD-GPU/ROCm hier irrelevant,
  82M-Modell läuft in Echtzeit auf dem 5900X): Zeile
  `--extra-index-url https://download.pytorch.org/whl/cpu` am Kopf der requirements.txt.
- `misaki[en]` (transitiv) enthält bereits `espeakng-loader` + `phonemizer-fork` —
  der deutsche G2P (`EspeakG2P(language='de')`) braucht nichts Weiteres.
- **System-Voraussetzungen** (auf dem Zielsystem bereits vorhanden): `espeak-ng`, `ffmpeg`.
  Wer das `backend/Dockerfile` nutzt, muss beide dort per `apt-get install -y espeak-ng ffmpeg`
  ergänzen (nicht Teil dieser Aufgabe, nur Hinweis als Kommentar im Dockerfile).

### Neuer Service `backend/services/tts.py`

Konstanten:

```python
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
```

Funktionen:

- `_load_pipeline() -> tuple[KPipeline, str]` — lazy Singleton hinter `threading.Lock()`
  (FastAPI-sync-Endpoints laufen im Threadpool). Lädt per `hf_hub_download` config, Gewichte
  und Voice (landen im HF-Cache `~/.cache/huggingface`), dann:
  `KModel(repo_id=MODEL_REPO, config=<config-pfad>, model=<pth-pfad>)` → `.eval()`,
  `KPipeline(lang_code="d", model=<kmodel>, repo_id=MODEL_REPO, device="cpu")`.
  Wichtig: `config` und `model` als **lokale Pfade** übergeben, sonst versucht `KModel`
  einen Lookup in `KModel.MODEL_NAMES` (kennt unser Repo nicht → KeyError).
  Rückgabe: Pipeline + lokaler Pfad der Voice-Datei (Fork lädt Voices, die auf `.pt`
  enden, direkt als lokalen Pfad).
- `synthesize(text: str, cache_dir: Path) -> Path` — öffentliche API:
  1. `key = hashlib.sha256(f"{CACHE_TAG}:{text}".encode()).hexdigest()`,
     Ziel `cache_dir / "tts_cache" / f"{key}.wav"`. Existiert die Datei → sofort zurück
     (**vor** jedem Modell-Import/-Load, Cache-Hits dürfen kein Torch anfassen →
     `kokoro`/`torch`-Importe gehören in `_load_pipeline`, nicht auf Modulebene).
  2. Sonst: `pipeline(text, voice=<voice-pfad>, speed=1, split_pattern=SPLIT_PATTERN)`
     iterieren, `audio`-Chunks (`torch.FloatTensor`) zu einem `numpy`-Array konkatenieren,
     mit `soundfile.write` als WAV **24000 Hz** in eine Temp-Datei neben dem Cache schreiben.
  3. ffmpeg-Effekt: `ffmpeg -y -i <roh.wav> -af FFMPEG_FILTER <ziel.wav>` via
     `subprocess.run(..., check=True, capture_output=True)`; Temp-Datei löschen.
     Schlägt ffmpeg fehl → Exception durchreichen (wird zum HTTP 500).
  4. Rückgabe: Pfad der fertigen Cache-Datei.

### Neuer Endpoint (`backend/main.py`, neben `get_node`)

```python
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

Auth läuft automatisch über die globale `require_api_key`-Dependency. Erste Synthese
einer langen Notiz kann mehrere Sekunden dauern — akzeptiert, kein Streaming (YAGNI);
der Cache macht jeden Folgeaufruf instantan.

## Frontend

### `frontend/src/api/client.ts`

Neuer Helper `apiFetchBlob(path: string): Promise<Blob>` — identisch zu `apiFetch`
(gleicher `X-API-Key`-Header, gleiche `ApiError`-Behandlung), aber `response.blob()`
statt JSON. Nötig, weil ein `<audio src>` den API-Key-Header nicht mitschicken kann.

### `frontend/src/App.tsx` — Wiedergabe-Logik

- State: `ttsEnabled: boolean`, initial aus `localStorage.getItem("cbks-tts-enabled")`
  (Default **true**), Änderung schreibt zurück.
- State: `audioState: "idle" | "loading" | "playing"`.
- Ein `useRef<HTMLAudioElement | null>` + `useRef<number>` (Request-Laufnummer gegen
  Race bei schnellem Knotenwechsel: Antwort nur verwenden, wenn Laufnummer noch aktuell).
- `playNode(detail)`: laufende Wiedergabe stoppen (`pause()`, Objekt-URL `revokeObjectURL`),
  `apiFetchBlob(\`/nodes/${id}/audio\`)`, Audio erzeugen, `onended`/`onerror` → `idle`,
  abspielen → `playing`. Fehler → `pushError` (vorhandenes Toast-System) + `idle`.
  Bei HTTP 422 (leerer Inhalt) **kein** Toast, still ignorieren.
- `stopAudio()`: stoppen + `idle`.
- `useEffect` auf `selectedNode?.node.id`: wenn `ttsEnabled` und Inhalt nicht leer →
  `playNode`; Knoten abgewählt → `stopAudio()`. Cleanup beim Unmount.

### `frontend/src/components/StatsBar.tsx` — globaler Toggle

Neue Props `ttsEnabled: boolean`, `onToggleTts: () => void`. Button im vorhandenen
Button-Stil: `🔊 Vorlesen an` / `🔇 Vorlesen aus`.

### `frontend/src/components/NodeDetailPanel.tsx` — Play/Stop

Neue Props `audioState`, `onPlayAudio: () => void`, `onStopAudio: () => void`.
Neben dem Titel ein Button: bei `idle` ▶ (startet Wiedergabe auch, wenn Auto-Play aus
ist), bei `loading` deaktiviert mit ⏳, bei `playing` ⏹ (stoppt). Nur rendern, wenn
`node.content` nicht leer ist.

## Fehlerbehandlung

| Fall | Verhalten |
|---|---|
| Node ohne Inhalt | Backend 422; Frontend ignoriert still (Auto-Play), Button wird gar nicht gezeigt |
| Erste Synthese (Modell-Download ~330 MB) | Dauert einmalig; Frontend zeigt ⏳ (`loading`) |
| ffmpeg/Kokoro-Fehler | HTTP 500 → Toast über vorhandenes `pushError` |
| Schneller Knotenwechsel | Laufnummer-Guard verwirft veraltete Antworten; alte Wiedergabe stoppt sofort |

## Tests (`backend/tests/test_api.py` erweitern, vorhandene Muster nutzen)

`backend.services.tts.synthesize` wird gemockt (monkeypatch, schreibt Mini-WAV-Datei):

1. `GET /nodes/{id}/audio` → 200, `content-type: audio/wav`, Body == Mock-Datei.
2. Unbekannte Node-ID → 404.
3. Node mit leerem `content` → 422.
4. Cache-Logik als Unit-Test für `tts.synthesize`: interne Generierung gemockt
   (monkeypatch `_load_pipeline`/Generierungsfunktion), zweiter Aufruf mit gleichem Text
   ruft die Generierung nicht erneut auf (Datei existiert bereits).

Der echte Kokoro-Pfad (Modell-Download + Synthese + ffmpeg) wird **manuell** verifiziert:
Backend starten, Knoten anklicken, Hörprobe — kein Modell-Download in CI/Tests.

## Nicht in Scope

- Streaming/Chunked-Audio, Wiedergabe-Fortschrittsbalken, Geschwindigkeitsregler
- Weitere Stimmen/Konfigurierbarkeit der Stimme
- Vorlesen von Titel, Metriken oder Suchergebnissen
- Docker-Image-Anpassung (nur Hinweis-Kommentar)

## Risiken

- **Fork-Verfügbarkeit:** `Thomcle/kokoro_german` ist ein kleines Community-Repo; per
  Commit-Hash gepinnt. Verschwindet es, wird der `kokoro/`-Ordner (Apache-2.0) nach
  `backend/vendor/` vendored — Architektur bleibt identisch.
- **Voicepack-Kompatibilität:** `martin.pt` stammt vom selben Maintainer/Training wie die
  Gewichte (Stage-2-Fine-Tune) — geringes Risiko. Verifikation über den manuellen Hörtest
  als erster Implementierungsschritt (Smoke-Test-Skript vor allem UI-Code).
