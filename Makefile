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

.PHONY: help up down bootstrap migrate revision seed test lint fmt run install

help:
	@echo "Comandos disponiveis:"
	@echo "  make install    - instala deps no venv (-e .[dev])"
	@echo "  make up          - sobe docker-compose (api, postgres, redis)"
	@echo "  make down        - derruba docker-compose"
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
