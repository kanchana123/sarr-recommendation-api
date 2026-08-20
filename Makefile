.PHONY: help install install-dev install-mcp test lint format run-api run-mcp latency docker-build docker-up docker-down docker-logs docker-build-api docker-build-lambda gcp-setup-vertex deploy-lambda deploy-lambda-guided deploy-lambda-full sam-build sam-deploy-guided sam-deploy sam-delete

SAM_TEMPLATE := infra/template.yaml
SAM_STACK ?= sarr-search
PYTHON := $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python3)

help:
	@echo "SARR common targets:"
	@echo "  make install              Install API deps"
	@echo "  make install-dev          Install all optional deps including tests"
	@echo "  make install-mcp          Install MCP stdio server deps (sarr[mcp])"
	@echo "  make run-api              Run search API locally on :8080"
	@echo "  make run-mcp              Run MCP stdio server (run make install-mcp first)"
	@echo "  make test                 Run unit tests"
	@echo "  make lint                 Ruff check"
	@echo "  make docker-build         Build sarr-api:latest image"
	@echo "  make docker-up            Start API container (uses .env → Qdrant Cloud)"
	@echo "  make docker-down          Stop compose stack"
	@echo "  make docker-logs          Tail API container logs"
	@echo "  make latency              Warmup + 50 sequential searches (uses SARR_API_URL)"
	@echo "  make docker-build-lambda  Build Lambda container image (linux/amd64)"
	@echo "  make gcp-setup-vertex     GCP SA + Secrets Manager for Vertex Gemini on Lambda"
	@echo "  make deploy-lambda        Build + deploy Lambda (uses infra/deploy.env or samconfig.toml)"
	@echo "  make deploy-lambda-guided First-time SAM deploy (writes samconfig.toml)"
	@echo "  make deploy-lambda-full   gcp-setup-vertex then deploy-lambda"
	@echo "  make sam-build            SAM build Lambda image from infra/template.yaml"
	@echo "  make sam-deploy-guided    First deploy (prompts for Qdrant + stack name)"
	@echo "  make sam-deploy           Redeploy using saved samconfig.toml"
	@echo "  make sam-delete           Tear down stack (SAM_STACK=$(SAM_STACK))"

install:
	pip install -e ".[api]"

install-dev:
	pip install -e ".[all]"

install-mcp:
	$(PYTHON) -m pip install -e ".[mcp]"

run-api:
	$(PYTHON) -m uvicorn sarr.api.app:app --host 0.0.0.0 --port 8080

run-mcp: install-mcp
	$(PYTHON) -m sarr.mcp.server

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
	docker build --platform linux/amd64 -f docker/Dockerfile.lambda -t sarr-search-api:lambda .

# Vertex Gemini on Lambda — run before deploy when using the LLM checkbox on the hosted UI.
gcp-setup-vertex:
	bash scripts/setup_gcp_vertex_auth.sh

deploy-lambda:
	bash scripts/deploy_lambda.sh

deploy-lambda-guided:
	bash scripts/deploy_lambda.sh --guided

deploy-lambda-full:
	bash scripts/deploy_lambda.sh --with-gcp-auth

# AWS Lambda + API Gateway (see infra/README.md).
sam-build:
	sam build --template $(SAM_TEMPLATE) --use-container

sam-deploy-guided: sam-build
	sam deploy --guided --template .aws-sam/build/template.yaml

sam-deploy: sam-build
	sam deploy

sam-delete:
	sam delete --stack-name $(SAM_STACK)
