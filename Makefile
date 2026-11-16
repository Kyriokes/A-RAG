# Comandos estables del proyecto. Los que todavía no existen fallan con
# "se implementa en la Etapa N". Con ENV=prod apuntan a producción.
SHELL := /bin/bash
ENV ?= local
SPLIT ?= dev
PY := uv run python
export ENV

.PHONY: setup db migrate corpus validate sample index parity dev test lint smoke eval-fast eval examples e2e

setup:
	uv sync

db:
	docker compose up -d db

migrate:
	$(PY) -m app.db migrate

corpus:
	$(PY) -m app.corpus.process

validate:
	$(PY) -m app.corpus.validate

sample:
	$(PY) -m app.corpus.sample --n 20

index:
	$(PY) -m app.index

parity:
	$(PY) -m app.parity

dev:
	uv run uvicorn app.api:app --reload --port 8000

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .

smoke:
	$(PY) -m evals.smoke

eval-fast:
	$(PY) -m evals.run --mode retrieval --split $(SPLIT)

eval:
	$(PY) -m evals.run --mode full --split $(SPLIT) $(if $(CONFIRM_TEST),--confirm-test,)

examples:
	@echo "se implementa en la Etapa 8" && exit 1

e2e:
	@echo "se implementa en la Etapa 8" && exit 1
