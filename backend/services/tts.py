from __future__ import annotations

import hashlib
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from backend.models.nodes import Node
from backend.services.parsing import parse_frontmatter

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


# ---------------------------------------------------------------------------
# Vorlesetext aus Node: Frontmatter strippen, Daten als Wort ausschreiben.
# ---------------------------------------------------------------------------

_GERMAN_BASE_NUMBERS = {
    0: "null", 1: "eins", 2: "zwei", 3: "drei", 4: "vier", 5: "fünf",
    6: "sechs", 7: "sieben", 8: "acht", 9: "neun",
    10: "zehn", 11: "elf", 12: "zwölf",
    13: "dreizehn", 14: "vierzehn", 15: "fünfzehn", 16: "sechzehn",
    17: "siebzehn", 18: "achtzehn", 19: "neunzehn",
}
_GERMAN_TENS = {
    2: "zwanzig", 3: "dreißig", 4: "vierzig", 5: "fünfzig",
    6: "sechzig", 7: "siebzig", 8: "achtzig", 9: "neunzig",
}
_GERMAN_ORDINAL_DAYS = {
    1: "ersten", 2: "zweiten", 3: "dritten", 4: "vierten", 5: "fünften",
    6: "sechsten", 7: "siebten", 8: "achten", 9: "neunten", 10: "zehnten",
    11: "elften", 12: "zwölften",
    13: "dreizehnten", 14: "vierzehnten", 15: "fünfzehnten", 16: "sechzehnten",
    17: "siebzehnten", 18: "achtzehnten", 19: "neunzehnten",
}
_GERMAN_MONTHS = {
    1: "Januar", 2: "Februar", 3: "März", 4: "April", 5: "Mai", 6: "Juni",
    7: "Juli", 8: "August", 9: "September", 10: "Oktober", 11: "November",
    12: "Dezember",
}


def _german_under_100(n: int) -> str:
    if n in _GERMAN_BASE_NUMBERS:
        return _GERMAN_BASE_NUMBERS[n]
    tens, ones = divmod(n, 10)
    if ones == 0:
        return _GERMAN_TENS[tens]
    ones_word = "ein" if ones == 1 else _GERMAN_BASE_NUMBERS[ones]
    return f"{ones_word}und{_GERMAN_TENS[tens]}"


def _german_ordinal_day(day: int) -> str:
    if day in _GERMAN_ORDINAL_DAYS:
        return _GERMAN_ORDINAL_DAYS[day]
    return _german_under_100(day) + "sten"


def _german_year(year: int) -> str:
    if year == 2000:
        return "zweitausend"
    if 2001 <= year <= 2099:
        return "zweitausend" + _german_under_100(year - 2000)
    return str(year)


def _format_german_date(iso_str: str) -> str:
    """ISO-Datum/Zeit → 'vierten Juli zweitausendsechsundzwanzig' (Akkusativ nach 'am')."""
    if not iso_str:
        return ""
    date_part = iso_str.strip().split("T")[0]
    try:
        year, month, day = (int(x) for x in date_part.split("-")[:3])
    except (ValueError, AttributeError):
        return iso_str
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return iso_str
    return f"{_german_ordinal_day(day)} {_GERMAN_MONTHS[month]} {_german_year(year)}"


def build_speech_text(node: Node) -> str:
    """Baut den vorlesbaren Text: Titel, Body (ohne Frontmatter), Erstellt/Geaendert als Wort.

    'Geaendert am' wird NUR ausgegeben, wenn ein echtes updated-Datum existiert
    (Frontmatter oder node.metadata['updated']). last_access ist kein Modifikations-
    datum sondern letzter Zugriff und wuerde eine falsche "Geaendert"-Aussage geben.
    """
    body, meta = parse_frontmatter(node.content or "")
    title = meta.get("title") or node.title
    created_raw = meta.get("created") or node.creation_time
    updated_raw = meta.get("updated") or node.metadata.get("updated")

    parts: list[str] = []
    if title:
        parts.append(title.rstrip("."))
    if body:
        parts.append(body.strip())
    if created_raw:
        parts.append(f"Erstellt am {_format_german_date(created_raw)}.")
    if updated_raw and updated_raw != created_raw:
        parts.append(f"Geändert am {_format_german_date(updated_raw)}.")
    return ". ".join(parts).strip()


class TTSUnavailableError(RuntimeError):
    """TTS-Abhaengigkeiten sind nicht installiert (backend/requirements-tts.txt)."""


def _load_pipeline() -> tuple[Any, str]:
    # Lazy Singleton hinter Lock: FastAPI-sync-Endpoints laufen im Threadpool,
    # das Modell darf nur einmal pro Prozess geladen werden.
    global _pipeline, _voice_path
    if _pipeline is None:
        with _pipeline_lock:
            if _pipeline is None:
                try:
                    from huggingface_hub import hf_hub_download
                    from kokoro import KModel, KPipeline
                except ImportError as exc:
                    raise TTSUnavailableError(
                        "TTS ist nicht installiert. Nachinstallieren mit: "
                        "pip install -r backend/requirements-tts.txt "
                        "(benoetigt Python <3.13 sowie espeak-ng und ffmpeg)"
                    ) from exc

                config_path = hf_hub_download(repo_id=CONFIG_REPO, filename=CONFIG_FILE)
                model_path = hf_hub_download(repo_id=MODEL_REPO, filename=MODEL_FILE)
                voice_path = hf_hub_download(repo_id=MODEL_REPO, filename=VOICE_FILE)

                kmodel = KModel(repo_id=MODEL_REPO, config=config_path, model=model_path).eval()
                pipeline = KPipeline(lang_code="d", model=kmodel, repo_id=MODEL_REPO, device="cpu")
                _voice_path = voice_path
                _pipeline = pipeline
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

    try:
        import soundfile as sf
    except ImportError as exc:
        raise TTSUnavailableError(
            "TTS ist nicht installiert. Nachinstallieren mit: "
            "pip install -r backend/requirements-tts.txt"
        ) from exc

    audio = _generate_audio(text)
    raw_path = tts_cache_dir / f"{key}.raw.wav"
    tmp_path = tts_cache_dir / f"{key}.tmp.wav"
    sf.write(raw_path, audio, 24000)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(raw_path), "-af", FFMPEG_FILTER, str(tmp_path)],
            check=True, capture_output=True,
        )
        os.replace(tmp_path, target_path)
    finally:
        raw_path.unlink(missing_ok=True)
        tmp_path.unlink(missing_ok=True)

    return target_path
