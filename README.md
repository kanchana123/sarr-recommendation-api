# SARR — Semantic Artifacts Retrieval and Ranking

SARR is a search system for software packages that ranks results by **meaning**,
not just keyword overlap. The MVP indexes **~168k PyPI packages** and answers
natural-language queries such as *“async HTTP client with retries”* or
*“machine learning”* — including packages whose names do not contain those words.

Offline indexing runs on GPU (Google Colab); online search serves query
embeddings, vector retrieval, optional cross-encoder reranking, and a
lightweight score blend over popularity and recency. Vectors live in
**Qdrant Cloud**; the API is **FastAPI** (local today, AWS Lambda-ready); the
demo UI is a static Vite app aimed at **GitHub Pages**.

---

## Why it exists

[PyPI.org](https://pypi.org) search is effective when you already know a package
name. It is less helpful when you only know the *problem*. SARR treats each
package as a short semantic document (name, description, keywords, stack hints)
and retrieves neighbors in embedding space, then reorders with maintenance and
adoption signals so popular, fresher projects tend to surface when relevance is
close.

---

## Architecture

```text
BigQuery (Libraries.io PyPI)
        │
        ▼
  Colab GPU ETL  ──embed──►  Qdrant Cloud
                              ▲
Client / Demo UI ──► FastAPI ─┘
                     embed query → ANN → optional rerank → blend → JSON
```

| Path | Role |
|---|---|
| **ETL** | Watermarked BigQuery extract → document build → bi-encoder batch embed → idempotent Qdrant upsert |
| **Online** | Query embed → top‑k vector search → optional cross-encoder → α·relevance + β·popularity + δ·recency |
| **Shared** | Same embedding model and search-document format for index and query (no train/serve skew) |

Designed so indexing (heavy, infrequent, GPU) stays separate from serving
(lightweight per request). The Lambda container path and SAM template are in
`docker/` and `infra/` for a serverless deploy without changing the search core.

---

## Retrieval & ranking

- **Bi-encoder:** `BAAI/bge-small-en-v1.5` (384‑d) for corpus and query vectors  
- **ANN:** cosine similarity over the full collection in Qdrant  
- **Rerank (optional):** `cross-encoder/ms-marco-MiniLM-L-6-v2` on a short top‑k list  
- **Blend:** semantic score mixed with stars / dependents / SourceRank and release recency  

Numeric popularity is kept in the **payload**, not stuffed into the embedding
text, so similarity stays about *what the package does*.

---

## Performance (MVP measurements)

| Stage | Observed |
|---|---|
| Full index load | **167,619** packages embedded + upserted to Qdrant (Colab T4) |
| Cold first query | multi‑second (model load on CPU) |
| Warm search, no rerank | typically **~0.5–1.7 s** locally → Qdrant Cloud |
| Warm search + rerank | often **~0.5–0.7 s** once both models are loaded (rerank adds work; cold starts dominate first calls) |

Latency is reported per request (`took_ms`, plus embed / Qdrant / rerank stages)
so regressions are visible during local testing.

ETL is **checkpointed by watermark** after each successful batch: a Colab
disconnect resumes without duplicating points (Qdrant upserts by stable package
id).

---

## Tech stack

| Layer | Choice |
|---|---|
| Data | BigQuery public Libraries.io (`projects` ⨝ `repositories`), PyPI filter |
| ML | sentence-transformers, PyTorch |
| Store | Qdrant Cloud |
| API | FastAPI, Pydantic v2, Mangum (Lambda) |
| Packaging | `src/` layout, `pyproject.toml`, optional extras (`api` / `etl` / `dev`) |
| UI | Vite static multi-page demo |
| Quality | pytest unit suite, Ruff, GitHub Actions CI |
| Deploy artifacts | Docker (local API + Lambda image), SAM template |

---

## Repository layout

```text
sarr-recommendation-api/
├── src/sarr/
│   ├── common/     # schemas, search-document builder, settings
│   ├── api/        # FastAPI, embedder, reranker, ranking, Qdrant client
│   └── etl/        # BigQuery extract → transform → embed → load
├── notebooks/      # Colab GPU ETL
├── frontend/       # Search · How it works · Contact
├── docker/         # API + Lambda images
├── infra/          # SAM (API Gateway + Lambda)
└── tests/          # unit (CI) + opt-in integration
```

---

## Quick start (local API + Qdrant Cloud)

```bash
cp .env.example .env   # set QDRANT_URL, QDRANT_API_KEY, QDRANT_COLLECTION
python3 -m venv .venv && source .venv/bin/activate
make install
make run-api           # http://localhost:8080
```

### CLI

Install once, then search without typing a URL (defaults to
`http://localhost:8080`, or `$SARR_API_URL` if set).

```bash
# terminal 1 — start the API (once)
cp .env.example .env   # Qdrant Cloud credentials
pip install -e ".[api]"
make run-api

# terminal 2 — use the CLI
pip install -e .       # lightweight client is enough when using the API
sarr health
sarr search "async HTTP client"
sarr search "machine learning" -n 5 --rerank
sarr search "http library" --json
```

Optional: point at another host without flags on every command:

```bash
export SARR_API_URL=https://your-deployed-api.example.com
sarr search "dataframe library"
```

In-process mode (no server; loads models locally):

```bash
pip install -e ".[api]"
sarr search "http client" --local
```

```bash
curl -s http://localhost:8080/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"http client library","limit":5,"rerank":false}'
```

```bash
cd frontend && cp .env.example .env && npm install && npm run dev
```

### Docker (recommended for reviewers)

Requires [Docker](https://docs.docker.com/get-docker/) + Compose. The API image
bundles FastAPI and the embedding stack; point it at **Qdrant Cloud** (the
indexed corpus) via `.env`.

```bash
git clone https://github.com/kanchana123/sarr-recommendation-api.git
cd sarr-recommendation-api
cp .env.example .env
# Edit .env — set at least:
#   QDRANT_URL=https://….aws.cloud.qdrant.io
#   QDRANT_API_KEY=…
#   QDRANT_COLLECTION=sarr

docker compose up --build -d
# or: make docker-up

curl -s http://localhost:8080/healthz
curl -s http://localhost:8080/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"machine learning","limit":5,"rerank":false}'

docker compose logs -f api    # first search downloads model weights (~once; cached in a volume)
docker compose down
```

Build the image alone:

```bash
docker build -f docker/Dockerfile.api -t sarr-api:latest .
docker run --rm -p 8080:8080 --env-file .env sarr-api:latest
```

Optional empty local Qdrant (no corpus until you run ETL):

```bash
QDRANT_URL=http://qdrant:6333 docker compose --profile local-qdrant up --build
```

### Deploy to AWS Lambda

Container image + API Gateway via SAM. See **[infra/README.md](infra/README.md)** for the full walkthrough.

```bash
sam build --template infra/template.yaml --use-container
sam deploy --guided
```

You will set `QdrantUrl`, `QdrantApiKey`, and `QdrantCollection` during deploy.
The stack output `ApiUrl` is your public search endpoint (`/v1/search`, `/healthz`).

### ETL (Colab)

Open `notebooks/etl_colab.ipynb`, use a GPU runtime, set billing `GCP_PROJECT_ID`
and Qdrant credentials, run the diagnostic count, then the full pipeline.
`LAST_UPDATE_DATE=1970-01-01` for the initial load; afterward the watermark file
drives incremental runs.

### Tests

```bash
make install-dev
make test
```

---

## API

| Method | Path | Notes |
|---|---|---|
| `GET` | `/healthz` | Liveness |
| `POST` | `/v1/search` | `{ "query", "limit", "rerank" }` → ranked hits + `took_ms` |

OpenAPI docs: `http://localhost:8080/docs`

---

## Roadmap

- Deploy search API on AWS Lambda + API Gateway  
- Incremental refresh from official PyPI BigQuery metadata (fresher releases) while preserving Libraries.io popularity fields  
- Hybrid sparse + dense retrieval for exact name matches  
- Larger eval set (nDCG) for ranking weight tuning  

---

## License

See [LICENSE](LICENSE).
