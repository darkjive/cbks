# Modell-Benchmark: Qwen3 4B vs. 8B vs. 14B

Datum: 2026-07-04

## Messkriterien (Spec §3.4)
- Korrektheit der Entitäten-Extraktion (manuell bewertet, 1–5 pro Event)
- Klassifikationsgenauigkeit (richtig/falsch pro Event)
- Latenz pro Event (Sekunden, aus Skript-Output)

Alle 15 Aufrufe (3 Modelle × 5 Events) lieferten gültiges, parsebares JSON — kein
Python-Traceback, kein Parsing-Fehler. Rohdaten: siehe Skript-Ausgabe von
`scripts/benchmark_models.py`, ausgeführt gegen die lokale Ollama-Instanz
(`http://127.0.0.1:11434`).

## Detailbewertung pro Event

Erwartete Klassifikation je Event (aus `event_type` in der Fixture, nicht Teil
des Prompts): PDF→document, Git-Commit→commit, Notiz→note, Webseite→document,
Screenshot→screenshot.

| Event | Modell | Latenz (s) | Klassifikation | Korrekt? | Entitäten (roh) | Qualität (1–5) |
|---|---|---|---|---|---|---|
| PDF | 4b | 26.22 | document | ja | Grundlagen der Graphentheorie, J. Klein, DAGs, Abhängigkeitsauflösung, Diskrete Strukturen | 4 |
| PDF | 8b | 17.62 | document | ja | Grundlagen der Graphentheorie, gerichtete azyklische Graphen, Abhängigkeitsauflösung, J. Klein, TU Berlin | 4 |
| PDF | 14b | 45.15 | document | ja | gerichtete azyklische Graphen (DAGs), Abhängigkeitsauflösung, J. Klein, TU Berlin, Diskrete Strukturen | 4 |
| Git-Commit | 4b | 29.14 | commit | ja | Al Ain, faiss, IndexIDMap, AssertionError, Index | 4 |
| Git-Commit | 8b | 16.57 | commit | ja | 4f2a9c1, Al Ain, faiss, IndexIDMap, remove_ids | 5 |
| Git-Commit | 14b | 7.98 | commit | ja | 4f2a9c1, Al Ain, faiss, remove_ids, IndexIDMap | 5 |
| Notiz | 4b | 24.94 | note | ja | german-sentiment-bert, Notizen, Emotionale Gewichtung, Wortwahl, Phase 3 | 3 |
| Notiz | 8b | 7.90 | note | ja | german-sentiment-bert, Phase 3, Emotionale Gewichtung, Notizen, Nutzer | 3 |
| Notiz | 14b | 10.84 | **task** | **nein** | Emotionale Gewichtung von Notizen, german-sentiment-bert, Phase 3 (nur 3 statt 5 Entitäten) | 3 |
| Webseite | 4b | 18.57 | document | ja | Ollama, OLLAMA_MAX_LOADED_MODELS, VRAM, models | 3 |
| Webseite | 8b | 13.82 | document | ja | Ollama Blog, OLLAMA_MAX_LOADED_MODELS, VRAM, multiple models, models | 3 |
| Webseite | 14b | 9.91 | document | ja | Ollama Blog, Running multiple models concurrently, OLLAMA_MAX_LOADED_MODELS, VRAM, Modelle | 4 |
| Screenshot | 4b | 14.05 | screenshot | ja | VS-Code, pytest, ROCm, event_log.py, append | 5 |
| Screenshot | 8b | 4.91 | screenshot | ja | VS-Code, pytest, ROCm, event_log.py, append() | 5 |
| Screenshot | 14b | 5.48 | screenshot | ja | VS-Code, pytest, ROCm, event_log.py, append() | 5 |

Auffälligkeiten:
- **qwen3:14b klassifiziert das Notiz-Event fehlerhaft als „task"** statt „note"
  — das einzige falsch klassifizierte Event im gesamten Testlauf, trotz größtem
  Modell. Vermutlich, weil der Text mit „Idee für später" beginnt, was der
  Formulierung einer Aufgabe ähnelt. 14b liefert hier zudem nur 3 statt bis zu
  5 angeforderten Entitäten.
- 4b verpasst beim Commit-Event sowohl den Commit-Hash (`4f2a9c1`) als auch den
  Funktionsnamen (`remove_ids`) — beides erfasst 8b korrekt (Qualität 4 vs. 5).
  Das ist die einzige Event/Modell-Kombination, bei der sich ein Qualitätsunterschied
  auch tatsächlich in der Bewertung niederschlägt (PDF ist bei allen drei Modellen
  gleich mit 4 bewertet, trotz leicht unterschiedlicher Entitätenlisten — „TU Berlin"
  fehlt bei 4b, wurde aber nicht als eigener Abzug gewertet). In einem
  Wissensmanagement-System sind gerade Commit-Hash und Funktionsname wichtige
  Entitäten für Graph-Verknüpfung/Deduplizierung.
- Latenz verhält sich in diesem Lauf nicht monoton mit der Modellgröße
  (4b ist im Schnitt langsamer als 8b). Das liegt an der variablen
  „Thinking"-Länge, die alle drei Qwen3-Modelle standardmäßig nutzen und die
  nicht strikt mit der Parametergröße korreliert — kein strukturelles
  Geschwindigkeitsargument für 4b.

## Ergebnistabelle

| Modell | Ø Latenz | Klassifikation korrekt (von 5) | Entitäten-Qualität (Ø 1–5) |
|---|---|---|---|
| qwen3:4b | 22.58s | 5/5 | 3.8 |
| qwen3:8b | 12.16s | 5/5 | 4.0 |
| qwen3:14b | 15.87s | 4/5 | 4.2 |

## Entscheidung

Gewähltes Modell: `qwen3:8b`

Begründung: Spec-Regel „kleinstes ausreichendes Modell gewinnt" — daher zuerst
geprüft, ob 4b bereits ausreicht. 4b klassifiziert zwar alle 5 Events korrekt,
zeigt aber beim Commit-Event eine konkrete Schwäche bei der Entitäten-Extraktion
(fehlender Commit-Hash/Funktionsname, Qualität 4 statt 5) und ist in diesem Lauf
zudem im Schnitt langsamer als 8b — es gibt also keinen Vorteil, der die
schwächere Extraktionsqualität aufwiegt. 8b behebt diese Lücke, klassifiziert
ebenfalls 5/5 korrekt und ist im Schnitt am schnellsten aller drei Modelle im
Testlauf.

14b bringt gegenüber 8b keinen belastbaren Zusatznutzen: Die Entitätenqualität
liegt nur marginal höher (4.2 vs. 4.0), während die Klassifikation bei einem
Event (Notiz → fälschlich „task") sogar schlechter ausfällt und die Latenz
beim PDF-Event mit 45s deutlich höher liegt als bei 8b (17.6s). Ein größeres
Modell, das eine reale Fehlklassifikation produziert, erfüllt die Kriterien
schlechter, nicht besser — es gibt daher keinen Grund, über 8b hinauszugehen.

`qwen3:8b` ist somit das kleinste Modell, das alle Messkriterien im Test ohne
konkrete Abstriche erfüllt. Referenz für Phase 2: `QWEN_MODEL=qwen3:8b`.
