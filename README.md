# SARR — Semantic Artifacts Retrieval and Ranking

Search API for software packages (MVP: **PyPI**). Documents are embedded offline
in **Google Colab (GPU)**, stored in **Qdrant Cloud**, and queried via **AWS Lambda**
(query embedding + optional rerank). Demo UI is hosted on **GitHub Pages**.

## Repository layout

```text
sarr-recommendation-api/
├── src/sarr/                 # Installable Python package (src layout)
│   ├── common/               # Shared schemas, search-doc builder, config
│   ├── api/                  # FastAPI app, Lambda handler, search pipeline
│   └── etl/                  # BigQuery → embed → Qdrant (used by Colab)
├── notebooks/                # Colab ETL notebook
├── frontend/                 # Vite demo UI (GitHub Pages)
├── docker/                   # API + Lambda Dockerfiles
├── infra/                    # SAM template for API Gateway + Lambda
├── tests/
│   ├── unit/                 # Fast isolated tests (default CI)
│   └── integration/          # Opt-in live Qdrant tests
├── data/                     # Local watermark file (gitignored contents)
├── docker-compose.yml        # Local API + Qdrant
├── pyproject.toml
└── Makefile
```

## Quick start (local)

```bash
cp .env.example .env
python -m venv .venv && source .venv/bin/activate
make install-dev
make test
make docker-up          # API on :8080, Qdrant on :6333
```

```bash
cd frontend && npm install && npm run dev
```

## ETL (Colab)

Open `notebooks/etl_colab.ipynb`, set GPU runtime, configure env vars, then run.
Use `LAST_UPDATE_DATE=1970-01-01` for the first full load; advance the watermark
for incremental updates.

## Lambda image

```bash
make docker-build-lambda
# Push to ECR, deploy with infra/template.yaml (SAM)
```

## API

- `GET /healthz`
- `POST /v1/search` — `{ "query": "...", "limit": 10, "rerank": false }`
