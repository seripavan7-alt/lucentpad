.DEFAULT_GOAL := help
SHELL := /bin/bash

.PHONY: help install dev down check check-py check-web check-contract fmt test contract demo

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

install: ## Install Python and dashboard dependencies
	uv sync
	cd dashboard && npm ci

dev: ## Run Postgres, API and dashboard (http://localhost:5173) with sample data
	docker compose up --build

down: ## Stop the dev stack and delete its data
	docker compose down -v

check: check-contract check-py check-web ## Everything CI runs

check-py: ## Lint, typecheck and test the Python code
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy server
	uv run pytest

check-web: ## Lint, typecheck, test and build the dashboard
	cd dashboard && npm run check

check-contract: ## Fail if contracts/ or generated dashboard types are stale
	@tmp=$$(mktemp -d); \
	uv run python -m prism_server.export_openapi $$tmp/openapi.json && \
	diff -u contracts/openapi.json $$tmp/openapi.json && \
	(cd dashboard && npx --no-install openapi-typescript ../contracts/openapi.json -o $$tmp/schema.d.ts >/dev/null) && \
	diff -u dashboard/src/api/schema.d.ts $$tmp/schema.d.ts || \
	{ echo "contract drift: run 'make contract'"; exit 1; }

contract: ## Regenerate contracts/openapi.json and dashboard API types
	uv run python -m prism_server.export_openapi contracts/openapi.json
	cd dashboard && npm run gen:api

fmt: ## Auto-format
	uv run ruff format .
	uv run ruff check --fix .
	cd dashboard && npm run fmt

test: ## Run tests only
	uv run pytest
	cd dashboard && npm test

demo: ## M4: one-command demo with seeded data (not yet implemented)
	@echo "make demo arrives in M4; use 'make dev' for now"; exit 1
