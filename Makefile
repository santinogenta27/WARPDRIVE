# Usa el Python del entorno virtual .venv si existe; si no, el del sistema (CI).
ifeq ($(OS),Windows_NT)
VENV_PY := .venv/Scripts/python.exe
else
VENV_PY := .venv/bin/python
endif
PYTHON ?= $(if $(wildcard $(VENV_PY)),$(VENV_PY),python)

export PYTHONPATH := backend

.PHONY: install dev migrate test evals lint api worker whatsapp-setup

install:
	$(PYTHON) -m pip install -r backend/requirements-dev.txt

dev:
	docker compose up -d db

test:
	$(PYTHON) -m pytest

evals:
	$(PYTHON) evals/runner.py

# WhatsApp (ver docs/whatsapp.md): API con el webhook, worker que corre el agente y alta de Acme.
api:
	$(PYTHON) -m uvicorn app.main:app --port 8000

worker:
	$(PYTHON) -m app.jobs

whatsapp-setup:
	$(PYTHON) -m app.demo.whatsapp_setup

lint:
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m ruff check .
	$(PYTHON) -m mypy
