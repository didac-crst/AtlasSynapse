.PHONY: help infra-up infra-down docs-check

help:
	@echo "AtlasSynapse scaffold targets: infra-up infra-down docs-check"

infra-up:
	docker compose up -d postgres

infra-down:
	docker compose down

docs-check:
	@echo "Documentation scaffold present. Implementation checks begin in Phase 0."
