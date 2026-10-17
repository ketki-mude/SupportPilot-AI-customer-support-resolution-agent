COMPOSE ?= docker compose
BACKEND := $(COMPOSE) run --rm backend
BACKEND_TEST := $(COMPOSE) -f docker-compose.yml -f docker-compose.test.yml run --rm backend
BACKEND_NODEPS := $(COMPOSE) run --rm --no-deps backend
MOCK := $(COMPOSE) run --rm --no-deps mock_commerce
FRONTEND := $(COMPOSE) run --rm --no-deps frontend
PLAYWRIGHT_IMAGE := mcr.microsoft.com/playwright:v1.64.1-noble
# Recorded in every evaluation report; "-dirty" marks uncommitted changes.
GIT_COMMIT := $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)$(shell test -z "$$(git status --porcelain 2>/dev/null)" || echo -dirty)
EVAL_RUN := $(COMPOSE) -f docker-compose.yml -f docker-compose.test.yml run --rm -e EVAL_GIT_COMMIT=$(GIT_COMMIT) backend python /evals/run_evals.py

.PHONY: up down migrate seed ingest test test-backend test-mock test-frontend coverage e2e eval eval-smoke lint lint-backend lint-frontend fmt logs

up: ## Build and start every service, waiting until they are healthy
	EVAL_GIT_COMMIT=$(GIT_COMMIT) $(COMPOSE) up -d --build --wait

down: ## Stop every service (data volume is kept)
	$(COMPOSE) down

migrate: ## Apply database migrations
	$(BACKEND) alembic upgrade head

seed: ## Create or update the demo users
	$(BACKEND) python -m scripts.seed

ingest: ## Embed the policy documents into policy_chunks
	$(BACKEND) python -m scripts.ingest_policies

test: test-backend test-mock test-frontend ## Run every test suite

test-backend:
	$(BACKEND_TEST) pytest

test-mock:
	$(MOCK) pytest

test-frontend:
	$(FRONTEND) npm test

coverage: ## Backend tests with a coverage report (terminal summary, HTML in backend/htmlcov)
	$(BACKEND_TEST) pytest --cov --cov-report=term --cov-report=html

e2e: ## Browser test of the full approval flow on the running stack (resets the mock shop's data)
	$(COMPOSE) restart mock_commerce && $(COMPOSE) up -d --wait mock_commerce
	docker run --rm --network supportpilot_default --env-file .env \
		-e E2E_BASE_URL=$${E2E_BASE_URL:-http://frontend:5173} -e E2E_MOCK_URL=http://mock_commerce:8001 \
		-v "$(CURDIR)/frontend:/work" -v supportpilot_e2e_node_modules:/work/node_modules -w /work \
		$(PLAYWRIGHT_IMAGE) sh -c "npm ci --no-audit --no-fund > /dev/null && npx playwright test"

eval: ## Full evaluation with the live model (needs GOOGLE_API_KEY; shows the estimated cost first). Extra flags: EVAL_ARGS="--limit 10"
	$(COMPOSE) up -d --wait mock_commerce_eval
	$(EVAL_RUN) $(EVAL_ARGS)

eval-smoke: ## First 10 cases with the offline keyword stand-in, as CI runs them (no model calls)
	$(COMPOSE) up -d --wait mock_commerce_eval
	$(EVAL_RUN) --llm mock --limit 10 --gate --min-correct 1 --no-docs

lint: lint-backend lint-frontend ## Ruff (lint and format check) and mypy for both Python services, tsc for the frontend

lint-backend:
	$(BACKEND_NODEPS) sh -c "ruff check . && ruff format --check . && mypy app scripts alembic"
	$(MOCK) sh -c "ruff check . && ruff format --check . && mypy main.py"

lint-frontend:
	$(FRONTEND) npm run typecheck

fmt: ## Apply ruff fixes and formatting
	$(BACKEND_NODEPS) sh -c "ruff check --fix . && ruff format ."
	$(MOCK) sh -c "ruff check --fix . && ruff format ."

logs: ## Follow service logs
	$(COMPOSE) logs -f
