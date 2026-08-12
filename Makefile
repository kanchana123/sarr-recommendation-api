.PHONY: help install install-dev test lint format docker-up docker-down docker-build-api docker-build-lambda

help:
	@echo "SARR common targets:"
	@echo "  make install           Install base + API deps"
	@echo "  make install-dev       Install all optional deps including tests"
	@echo "  make test              Run unit tests"
	@echo "  make lint              Ruff check"
	@echo "  make docker-up         Start local API + Qdrant"
	@echo "  make docker-down       Stop compose stack"
	@echo "  make docker-build-api  Build local API image"
	@echo "  make docker-build-lambda  Build Lambda container image"

install:
	pip install -e ".[api]"

install-dev:
	pip install -e ".[all]"

test:
	pytest tests/unit -m unit

test-all:
	pytest

lint:
	ruff check src tests

format:
	ruff format src tests

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

docker-build-api:
	docker build -f docker/Dockerfile.api -t sarr-api:local .

docker-build-lambda:
	docker build -f docker/Dockerfile.lambda -t sarr-search-api:lambda .
