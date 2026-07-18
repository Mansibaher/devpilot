.PHONY: help up down logs test test-integration test-docker test-docker-cold cov lint fmt typecheck check migrate revision rebuild

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

up:  ## Start Postgres, run migrations, start the API
	docker compose up --build -d --wait
	@echo "API:    http://localhost:8000/docs"
	@echo "Health: http://localhost:8000/health"

down:  ## Stop the stack (volumes preserved)
	docker compose down

logs:  ## Tail API logs
	docker compose logs -f api

test:  ## Run unit tests (no database required)
	cd backend && python -m pytest -m "not integration"

test-integration:  ## Run all tests, including those needing live Postgres
	cd backend && python -m alembic upgrade head && python -m pytest

test-docker:  ## Run the full suite inside the running api container
	docker compose exec api pytest

test-docker-cold:  ## Run the full suite in a throwaway container (starts db + migrations)
	docker compose run --rm api pytest

rebuild:  ## Rebuild images after changing dependencies or the Dockerfile
	docker compose build --no-cache api

cov:  ## Run tests with a coverage report
	cd backend && python -m pytest --cov=devpilot --cov-report=term-missing

lint:  ## Lint
	cd backend && python -m ruff check src tests

fmt:  ## Format
	cd backend && python -m ruff format src tests && python -m ruff check --fix src tests

typecheck:  ## Type-check
	cd backend && python -m mypy

check: lint typecheck test  ## Run everything CI runs

migrate:  ## Apply migrations
	cd backend && python -m alembic upgrade head

revision:  ## Autogenerate a migration: make revision m="add users"
	cd backend && python -m alembic revision --autogenerate -m "$(m)"
