# Makefile da Plataforma de Inteligência Fiscal (backend)
#
# No Windows sem GNU make instalado, rode os comandos equivalentes com o
# Python do venv, ex.:  ./.venv/Scripts/python.exe -m pytest
# Aqui PY aponta para o interpretador do venv (ajuste se necessário).

ifeq ($(OS),Windows_NT)
	PY ?= ./.venv/Scripts/python.exe
else
	PY ?= ./.venv/bin/python
endif

.PHONY: help up down redis worker worker-logs queue-info smoke-infra bootstrap migrate revision seed test lint fmt run install

help:
	@echo "Comandos disponiveis:"
	@echo "  make install    - instala deps no venv (-e .[dev])"
	@echo "  make up          - sobe Docker (api, postgres, Redis e worker RQ)"
	@echo "  make down        - derruba docker-compose"
	@echo "  make redis       - sobe somente o Redis persistente"
	@echo "  make worker      - sobe o worker (e dependencias saudaveis)"
	@echo "  make worker-logs - acompanha os logs do worker"
	@echo "  make queue-info  - mostra filas, workers e contagens do RQ"
	@echo "  make smoke-infra - valida Redis, migration/head, API e RQ"
	@echo "  make bootstrap   - cria banco/role/extensao no Postgres local"
	@echo "  make migrate     - alembic upgrade head"
	@echo "  make revision m= - nova migration (autogenerate)"
	@echo "  make seed        - carrega 1 municipio + 1 estado de exemplo"
	@echo "  make test        - pytest"
	@echo "  make lint        - ruff + mypy"
	@echo "  make fmt         - ruff format + fix"
	@echo "  make run         - uvicorn (dev)"

install:
	$(PY) -m pip install -e ".[dev]"

up:
	docker compose up -d --build

down:
	docker compose down

redis:
	docker compose up -d redis

worker:
	docker compose up -d --build worker

worker-logs:
	docker compose logs -f --tail=200 worker

queue-info:
	docker compose exec -T worker rq info --url redis://redis:6379/0

smoke-infra:
	docker compose exec -T redis redis-cli ping
	docker compose exec -T api alembic current
	docker compose exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read().decode())"
	docker compose exec -T worker rq info --url redis://redis:6379/0

bootstrap:
	$(PY) -m scripts.bootstrap_db

migrate:
	$(PY) -m alembic upgrade head

revision:
	$(PY) -m alembic revision --autogenerate -m "$(m)"

seed:
	$(PY) -m scripts.seed

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check src tests
	$(PY) -m mypy

fmt:
	$(PY) -m ruff format src tests
	$(PY) -m ruff check --fix src tests

run:
	$(PY) -m uvicorn app.main:app --reload --app-dir src
