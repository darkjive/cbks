import logging
import os
import tempfile
import threading
from pathlib import Path

import faiss
import numpy as np

logger = logging.getLogger("cbks.faiss")


class FaissIndex:
    def __init__(self, dim: int, index_path: Path):
        self._dim = dim
        self._path = index_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if self._path.exists():
            self._index = faiss.read_index(str(self._path))
            loaded_d = self._index.d
            if loaded_d != self._dim:
                raise ValueError(
                    f"FAISS-Index hat Dimension {loaded_d}, erwartet {self._dim} "
                    f"(Embedding-Modell geaendert? Index loeschen: {self._path})"
                )
        else:
            self._index = faiss.IndexIDMap(faiss.IndexFlatIP(dim))

    def add(self, faiss_id: int, vector: list[float]) -> None:
        try:
            normalized = self._normalize(vector)
        except ValueError:
            # Null-Vektor (leerer Text / Embedding-Fehler): nicht indexieren statt
            # den Ingest abzubrechen (add laeuft nach add_node, sonst bliebe ein
            # halb-committeter Node). Konsistent mit search(), das Null-Vektoren
            # ebenfalls ueberspringt. Der Node ist dann per Vektorsuche nicht
            # auffindbar, aber ueber Graph/Titel weiterhin erreichbar.
            logger.warning("Null-Vektor fuer faiss_id=%s, ueberspringe Index-Add", faiss_id)
            return
        with self._lock:
            self._index.add_with_ids(
                normalized.reshape(1, -1), np.array([faiss_id], dtype="int64")
            )

    def search(self, vector: list[float], k: int) -> list[tuple[int, float]]:
        with self._lock:
            if self._index.ntotal == 0:
                return []
        try:
            normalized = self._normalize(vector)
        except ValueError:
            # Null-Vektor (leerer Text / Embedding-Fehler): keine sinnvollen
            # Aehnlichkeiten mit IndexFlatIP moeglich -> leer statt Zufallstreffer.
            logger.warning("Suchanfrage ergab Null-Vektor, liefere keine Treffer")
            return []
        with self._lock:
            scores, ids = self._index.search(normalized.reshape(1, -1), k)
        results = []
        for faiss_id, score in zip(ids[0], scores[0]):
            if faiss_id == -1:
                continue
            results.append((int(faiss_id), float(score)))
        return results

    def remove(self, faiss_id: int) -> None:
        with self._lock:
            if self._index.ntotal == 0:
                return
            self._index.remove_ids(np.array([faiss_id], dtype="int64"))

    def save(self) -> None:
        # Atomares Schreiben: tmp-Datei + os.replace verhindert korrupte
        # Index-Dateien bei Absturz/Platzmangel mitten im Schreiben, sonst
        # schlaegt jeder folgende Startup fehl (faiss.read_index wirft).
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                prefix=".faiss_tmp_", suffix=".index", dir=str(self._path.parent)
            )
            try:
                os.close(fd)
                faiss.write_index(self._index, tmp_path)
                os.replace(tmp_path, str(self._path))
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

    def clear(self) -> None:
        with self._lock:
            self._index = faiss.IndexIDMap(faiss.IndexFlatIP(self._dim))
            if self._path.exists():
                self._path.unlink()

    @property
    def ntotal(self) -> int:
        with self._lock:
            return self._index.ntotal

    @staticmethod
    def _normalize(vector: list[float]) -> np.ndarray:
        array = np.array(vector, dtype="float32")
        norm = np.linalg.norm(array)
        if norm == 0:
            raise ValueError("Null-Vektor kann nicht normalisiert werden")
        return array / norm
