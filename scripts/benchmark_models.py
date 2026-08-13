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
