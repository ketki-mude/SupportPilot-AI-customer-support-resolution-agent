COMPOSE ?= docker compose
BACKEND := $(COMPOSE) run --rm backend
BACKEND_NODEPS := $(COMPOSE) run --rm --no-deps backend
MOCK := $(COMPOSE) run --rm --no-deps mock_commerce

.PHONY: up down migrate seed test test-backend test-mock lint fmt logs

up: ## Build and start every service, waiting until they are healthy
	$(COMPOSE) up -d --build --wait

down: ## Stop every service (data volume is kept)
	$(COMPOSE) down

migrate: ## Apply database migrations
	$(BACKEND) alembic upgrade head

seed: ## Create or update the demo users
	$(BACKEND) python -m scripts.seed

test: test-backend test-mock ## Run every test suite

test-backend:
	$(BACKEND) pytest

test-mock:
	$(MOCK) pytest

lint: ## Ruff (lint and format check) and mypy for both Python services
	$(BACKEND_NODEPS) sh -c "ruff check . && ruff format --check . && mypy app scripts alembic"
	$(MOCK) sh -c "ruff check . && ruff format --check . && mypy main.py"

fmt: ## Apply ruff fixes and formatting
	$(BACKEND_NODEPS) sh -c "ruff check --fix . && ruff format ."
	$(MOCK) sh -c "ruff check --fix . && ruff format ."

logs: ## Follow service logs
	$(COMPOSE) logs -f
