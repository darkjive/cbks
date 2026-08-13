# Root-Level-Einstiegspunkte für Backend und Frontend.
# Ohne diese Ziele müsste man beide Hälften einzeln von Hand verifizieren.

PYTHON ?= python3
VENV   ?= .venv
PIP    := $(VENV)/bin/pip
PY     := $(VENV)/bin/python

.PHONY: help setup test lint build check clean

help:  ## Diese Übersicht anzeigen
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup:  ## venv anlegen, Backend- und Frontend-Abhängigkeiten installieren
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r backend/requirements.txt
	cd frontend && npm install

test:  ## Backend-Testsuite (pytest)
	$(PY) -m pytest backend/tests -q

lint:  ## Frontend linten (oxlint)
	cd frontend && npm run lint

build:  ## Frontend typechecken und bauen (tsc -b && vite build)
	cd frontend && npm run build

check: test lint build  ## Alles verifizieren, was CI auch prüft

clean:  ## Build-Artefakte und Caches entfernen
	rm -rf frontend/dist .pytest_cache
	find . -type d -name __pycache__ -not -path "./$(VENV)/*" -exec rm -rf {} +
