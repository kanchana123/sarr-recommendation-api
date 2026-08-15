.PHONY: help install install-dev test lint format run-api latency docker-build docker-up docker-down docker-logs docker-build-api docker-build-lambda

help:
	@echo "SARR common targets:"
	@echo "  make install              Install API deps"
	@echo "  make install-dev          Install all optional deps including tests"
	@echo "  make run-api              Run search API locally on :8080"
	@echo "  make test                 Run unit tests"
	@echo "  make lint                 Ruff check"
	@echo "  make docker-build         Build sarr-api:latest image"
	@echo "  make docker-up            Start API container (uses .env → Qdrant Cloud)"
	@echo "  make docker-down          Stop compose stack"
	@echo "  make docker-logs          Tail API container logs"
	@echo "  make latency             Warmup + 50 sequential searches (uses SARR_API_URL)"

install:
	pip install -e ".[api]"

install-dev:
	pip install -e ".[all]"

run-api:
	uvicorn sarr.api.app:app --host 0.0.0.0 --port 8080

test:
	pytest tests/unit -m unit

test-all:
	pytest

latency:
	python3 scripts/measure_latency.py --url "$${SARR_API_URL:-http://localhost:8080}"

lint:
	ruff check src tests

format:
	ruff format src tests

docker-build:
	docker build -f docker/Dockerfile.api -t sarr-api:latest .

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f api

docker-build-api: docker-build

docker-build-lambda:
	docker build -f docker/Dockerfile.lambda -t sarr-search-api:lambda .
