.PHONY: help install infra-up infra-down migrate migrate-down seed lint format typecheck test ci docs-check

help:
	@echo "Targets: install infra-up infra-down migrate migrate-down seed lint format typecheck test ci"

install:
	python -m pip install -e ".[dev]"

infra-up:
	docker compose up -d postgres

infra-down:
	docker compose down

migrate:
	alembic upgrade head

migrate-down:
	alembic downgrade -1

seed:
	python -m semantic_memory.seeding

lint:
	ruff check src tests migrations

format:
	ruff format src tests migrations
	ruff check --fix src tests migrations

typecheck:
	mypy src

test:
	pytest -q

ci: lint typecheck test

docs-check:
	@test -f README.md && test -f docs/architecture.md && test -f docs/roadmap.md
