.DEFAULT_GOAL := help
VENV         := .venv
PYTHON       := $(VENV)/bin/python
PIP          := $(VENV)/bin/pip
UVICORN      := $(VENV)/bin/uvicorn
ALEMBIC      := $(VENV)/bin/alembic
PYTEST       := $(VENV)/bin/pytest
RUFF         := $(VENV)/bin/ruff

# ── Colours ──────────────────────────────────────────────────────────────────
BOLD  := \033[1m
CYAN  := \033[36m
GREEN := \033[32m
RESET := \033[0m

# ─────────────────────────────────────────────────────────────────────────────
# Help
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: help
help: ## Show this help message
	@printf "\n$(BOLD)PaySwitch Credit Scoring — available commands$(RESET)\n\n"
	@awk 'BEGIN {FS = ":.*##"} \
		/^[a-zA-Z_-]+:.*?##/ { printf "  $(CYAN)%-22s$(RESET) %s\n", $$1, $$2 } \
		/^##@/ { printf "\n$(BOLD)%s$(RESET)\n", substr($$0, 5) }' $(MAKEFILE_LIST)
	@printf "\n"

# ─────────────────────────────────────────────────────────────────────────────
##@ Setup
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: venv
venv: ## Create the virtual environment
	python3 -m venv $(VENV)
	@echo "$(GREEN)Virtual environment created at $(VENV)/$(RESET)"

.PHONY: install
install: venv ## Install all project dependencies into the venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	@echo "$(GREEN)Dependencies installed$(RESET)"

.PHONY: env
env: ## Copy .env.example to .env (skips if .env already exists)
	@if [ -f .env ]; then \
		echo ".env already exists — skipping"; \
	else \
		cp .env.example .env; \
		echo "$(GREEN).env created from .env.example — fill in your values$(RESET)"; \
	fi

.PHONY: setup
setup: install env ## Full first-time setup: venv + deps + .env
	@echo "$(GREEN)Setup complete. Edit .env then run: make migrate && make start$(RESET)"

# ─────────────────────────────────────────────────────────────────────────────
##@ Database
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: migrate
migrate: ## Run all pending Alembic migrations
	$(ALEMBIC) upgrade head

.PHONY: migrate-down
migrate-down: ## Roll back the last Alembic migration
	$(ALEMBIC) downgrade -1

.PHONY: migrate-history
migrate-history: ## Show Alembic migration history
	$(ALEMBIC) history --verbose

.PHONY: migrate-status
migrate-status: ## Show current migration revision
	$(ALEMBIC) current

.PHONY: makemigration
makemigration: ## Auto-generate a new migration (use: make makemigration msg="your message")
	$(ALEMBIC) revision --autogenerate -m "$(msg)"

.PHONY: superuser
superuser: ## Create the bootstrap admin user (reads BOOTSTRAP_ADMIN_* from .env)
	$(PYTHON) scripts/create_superuser.py

# ─────────────────────────────────────────────────────────────────────────────
##@ Run
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: start
start: ## Start the API server with hot-reload (development)
	$(UVICORN) app.main:app --host 0.0.0.0 --port 8000 --reload

.PHONY: start-prod
start-prod: ## Start the API server without hot-reload (production-like)
	$(UVICORN) app.main:app --host 0.0.0.0 --port 8000 --workers 4

.PHONY: run
run: migrate start ## Migrate then start (convenience for first run)

# ─────────────────────────────────────────────────────────────────────────────
##@ Docker
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: build
build: ## Build the Docker image
	docker build -t payswitch-api .

.PHONY: up
up: ## Start all services via Docker Compose
	docker compose up

.PHONY: up-build
up-build: ## Build and start all services via Docker Compose
	docker compose up --build

.PHONY: down
down: ## Stop all Docker Compose services
	docker compose down

.PHONY: down-volumes
down-volumes: ## Stop all Docker Compose services and delete volumes
	docker compose down -v

.PHONY: logs
logs: ## Tail Docker Compose logs (use: make logs s=api)
	docker compose logs -f $(s)

# ─────────────────────────────────────────────────────────────────────────────
##@ Testing
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: test
test: ## Run the full test suite
	$(PYTEST) tests/ -v

.PHONY: test-cov
test-cov: ## Run tests with coverage report
	$(PYTEST) tests/ -v --cov=app --cov-report=term-missing --cov-fail-under=60

.PHONY: test-critical-cov
test-critical-cov: ## Require 80% coverage across critical security/scoring paths
	$(PYTEST) tests/ -v \
		--cov=app.core.security \
		--cov=app.dependencies \
		--cov=app.services.hard_stops \
		--cov=app.services.scoring_pipeline \
		--cov=app.subscribers.batch_complete \
		--cov-fail-under=80 --cov-report=term-missing

.PHONY: test-integration
test-integration: ## Run integration tests only
	$(PYTEST) tests/ -v -m integration

.PHONY: test-unit
test-unit: ## Run unit tests only (exclude integration)
	$(PYTEST) tests/ -v -m "not integration"

# ─────────────────────────────────────────────────────────────────────────────
##@ Code Quality
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: lint
lint: ## Run ruff linter
	$(RUFF) check app/

.PHONY: lint-fix
lint-fix: ## Run ruff linter and auto-fix issues
	$(RUFF) check app/ --fix

.PHONY: format
format: ## Format code with ruff
	$(RUFF) format app/

.PHONY: check
check: lint ## Run all code quality checks
	$(RUFF) format --check app/ tests/ scripts/
	$(PYTHON) -m mypy app tests

.PHONY: verify-handover
verify-handover: ## Run quality, coverage, security, and manifest handover gates
	./scripts/verify_handover.sh

.PHONY: test-e2e
test-e2e: ## Run isolated Docker integration stack and 3-item batch E2E
	./scripts/run_handover_e2e.sh

.PHONY: test-e2e-local
test-e2e-local: ## Run isolated E2E with local Postgres and in-process Redis
	./scripts/run_handover_e2e_local.sh

# ─────────────────────────────────────────────────────────────────────────────
##@ Utilities
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: shell
shell: ## Open a Python shell inside the venv
	$(PYTHON) -i -c "import app"

.PHONY: openapi
openapi: ## Export the OpenAPI schema to openapi.json
	$(PYTHON) -c "import json; from app.main import app; print(json.dumps(app.openapi(), indent=2))" > openapi.json
	@echo "$(GREEN)openapi.json updated$(RESET)"

.PHONY: clean
clean: ## Remove venv, caches, and compiled files
	rm -rf $(VENV) __pycache__ .pytest_cache .ruff_cache \
		*.egg-info app/*.egg-info \
		$$(find . -type d -name __pycache__)
	@echo "$(GREEN)Clean complete$(RESET)"
