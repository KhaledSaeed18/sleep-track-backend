.DEFAULT_GOAL := help
SHELL := /bin/bash

UV ?= uv
COMPOSE ?= docker compose
OPENAPI_TARGETS ?= docs/openapi.json

.PHONY: help
help: ## Show available targets
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ---------------------------------------------------------------- setup
.PHONY: install
install: ## Install Python 3.13 and all dependencies, set up git hooks
	$(UV) python install 3.13
	$(UV) sync --all-extras
	$(UV) run pre-commit install

# ---------------------------------------------------------------- run
.PHONY: infra
infra: ## Start Postgres, Redis and Mailpit only
	$(COMPOSE) up -d postgres redis mailpit

.PHONY: dev
dev: infra migrate ## Run the API locally with auto-reload (jobs run in-process)
	JOBS_EAGER=true $(UV) run uvicorn app.main:create_app --factory --reload --port 8000

.PHONY: worker
worker: ## Run the ARQ worker locally
	$(UV) run arq app.worker.settings.WorkerSettings --watch app

.PHONY: up
up: ## Build and start the full stack (api, worker, migrate, postgres, redis, mailpit)
	$(COMPOSE) up -d --build

.PHONY: down
down: ## Stop the stack
	$(COMPOSE) down

.PHONY: logs
logs: ## Tail API and worker logs
	$(COMPOSE) logs -f api worker

# ---------------------------------------------------------------- database
.PHONY: migrate
migrate: ## Apply migrations
	$(UV) run alembic upgrade head

.PHONY: migration
migration: ## Autogenerate a migration: make migration m="add something"
	@test -n "$(m)" || (echo 'usage: make migration m="message"' && exit 1)
	$(UV) run alembic revision --autogenerate -m "$(m)"

.PHONY: seed
seed: ## Seed demo, admin and sample accounts (uses the compose stack when it is running)
	@if $(COMPOSE) ps --status running api 2>/dev/null | grep -q api; then \
		$(COMPOSE) exec api python -m app.scripts.seed; \
	else \
		$(UV) run python -m app.scripts.seed; \
	fi

.PHONY: create-admin
create-admin: ## Create or promote an admin: make create-admin email=you@example.com
	$(UV) run python -m app.scripts.create_admin --email "$(email)"

# ---------------------------------------------------------------- quality
.PHONY: lint
lint: ## Lint with ruff
	$(UV) run ruff check .
	$(UV) run ruff format --check .

.PHONY: format
format: ## Format and auto-fix with ruff
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

.PHONY: typecheck
typecheck: ## Type-check with mypy (strict)
	$(UV) run mypy

.PHONY: test
test: ## Run the full test suite with coverage (needs Postgres and Redis)
	$(UV) run pytest --cov --cov-report=term-missing --cov-report=xml

.PHONY: test-unit
test-unit: ## Run unit tests only (no services required)
	$(UV) run pytest tests/unit

.PHONY: check
check: lint typecheck test ## Lint, type-check and test

# ---------------------------------------------------------------- artefacts
.PHONY: openapi
openapi: ## Export the OpenAPI schema (OPENAPI_TARGETS="docs/openapi.json ../openapi.json")
	$(UV) run python -m app.scripts.export_openapi $(OPENAPI_TARGETS)

.PHONY: docker-build
docker-build: ## Build the production image
	docker build -t sleeptrack-api:latest .

.PHONY: clean
clean: ## Remove caches and build artefacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
