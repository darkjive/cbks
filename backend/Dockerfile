FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app

# Nur Build-Werkzeuge – KEIN ROCm nötig:
# Das Backend spricht mit Ollama nur per HTTP, GPU-Zugriff hat allein das native Ollama auf dem Host.
# espeak-ng und ffmpeg sind System-Binaries (kein pip-Paket), die das TTS-Feature
# (backend/services/tts.py) zur Laufzeit aufruft.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgomp1 \
    espeak-ng \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# requirements-tts.txt referenziert requirements.txt via "-r", daher beide kopieren.
# Das Image bringt TTS vollstaendig mit (espeak-ng/ffmpeg oben, Kokoro hier);
# Basis ist Python 3.11, wo der misaki-Constraint (<3.13) erfuellt ist.
COPY backend/requirements.txt backend/requirements-tts.txt backend/
RUN pip install --no-cache-dir -r backend/requirements-tts.txt

COPY backend/ backend/

RUN useradd -m cbksuser && chown -R cbksuser /app
USER cbksuser

# Kein --reload im Betrieb (Entwicklungsmodus).
# Host statt 0.0.0.0: Container läuft mit network_mode: host, daher muss
# Uvicorn selbst auf 127.0.0.1 binden, um "nur localhost" zu garantieren.
CMD ["uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000"]
