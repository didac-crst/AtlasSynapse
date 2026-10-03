.PHONY: help install infra-up infra-down migrate migrate-down seed lint format format-check typecheck test ci docs-check

PYTHON ?= python

help:
	@echo "Targets: install infra-up infra-down migrate migrate-down seed lint format format-check typecheck test ci"

install:
	$(PYTHON) -m pip install -e ".[dev]"

infra-up:
	docker compose up -d postgres

infra-down:
	docker compose down

migrate:
	$(PYTHON) -m alembic upgrade head

migrate-down:
	$(PYTHON) -m alembic downgrade -1

seed:
	$(PYTHON) -m semantic_memory.seeding

lint:
	$(PYTHON) -m ruff check src tests migrations

format-check:
	$(PYTHON) -m ruff format --check src tests migrations

format:
	$(PYTHON) -m ruff format src tests migrations
	$(PYTHON) -m ruff check --fix src tests migrations

typecheck:
	$(PYTHON) -m mypy src

test:
	$(PYTHON) -m pytest -q

ci: format-check lint typecheck test

docs-check:
	@test -f README.md && test -f docs/architecture.md && test -f docs/roadmap.md
