# Usa el Python del entorno virtual .venv si existe; si no, el del sistema (CI).
ifeq ($(OS),Windows_NT)
VENV_PY := .venv/Scripts/python.exe
else
VENV_PY := .venv/bin/python
endif
PYTHON ?= $(if $(wildcard $(VENV_PY)),$(VENV_PY),python)

export PYTHONPATH := backend

.PHONY: install dev migrate test evals evals-en lint api worker whatsapp-setup demo-setup demo-reset

install:
	$(PYTHON) -m pip install -r backend/requirements-dev.txt

dev:
	docker compose up -d db

test:
	$(PYTHON) -m pytest

evals:
	$(PYTHON) evals/runner.py

# Evals en inglés de Northwind Outfitters (decisión 038). Con claves usa el modelo real.
evals-en:
	$(PYTHON) evals/runner.py --company northwind

# Demo (ver docs/whatsapp.md y docs/chat-web.md): API con el chat web y los webhooks, worker
# que corre el agente para WhatsApp, y alta de Acme con sus canales.
api:
	$(PYTHON) -m uvicorn app.main:app --port 8000

worker:
	$(PYTHON) -m app.jobs

whatsapp-setup:
	$(PYTHON) -m app.demo.whatsapp_setup

# Crea o regenera las dos empresas de demo: Acme (en español) y Northwind Outfitters (en
# inglés, decisión 038). Idempotente. NORTHWIND_PASSWORD fija la clave del panel de Northwind.
demo-setup:
	$(PYTHON) -m app.demo.whatsapp_setup
	$(PYTHON) -m app.demo.northwind

# Northwind vuelve a empezar: archiva sus conversaciones de prueba.
demo-reset:
	$(PYTHON) -m app.demo.northwind --reset

lint:
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m ruff check .
	$(PYTHON) -m mypy
