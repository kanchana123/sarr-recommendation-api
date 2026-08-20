# Deploy SARR to AWS Lambda (container image)

End-to-end pipeline for the hosted search API and optional RAG (Vertex Gemini on Lambda).

## Prerequisites

| Tool | Purpose |
|---|---|
| [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) | Deploy stack, Secrets Manager |
| [SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) | Build + deploy Lambda container |
| [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) | Create Vertex service account (`gcloud auth login`) |
| Docker | SAM builds `linux/amd64` image locally |
| Qdrant Cloud | URL + API key + collection `sarr` |

## One-time setup

```bash
# 1) Local API secrets (local dev only)
cp .env.example .env

# 2) Deploy parameters (Qdrant + optional ECR repo after first deploy)
cp infra/deploy.env.example infra/deploy.env
# Edit infra/deploy.env — set QDRANT_URL, QDRANT_API_KEY, SAM_IMAGE_REPOSITORY
```

## Deployment pipeline (recommended)

From the **repo root**:

```bash
# A) Vertex Gemini credentials for Lambda (run before deploy, or when rotating keys)
make gcp-setup-vertex
# same as: ./scripts/setup_gcp_vertex_auth.sh

# B) First deploy — creates stack, ECR repo, samconfig.toml
make deploy-lambda-guided

# C) Later redeploys (code / template changes)
make deploy-lambda

# Or combine A + C in one command:
make deploy-lambda-full
```

### What the scripts do

| Script | Action |
|---|---|
| `scripts/setup_gcp_vertex_auth.sh` | Enables Vertex API; creates `sarr-lambda-vertex@…` SA; grants `aiplatform.user`; stores JSON key in Secrets Manager `sarr-search/gcp-vertex` |
| `scripts/deploy_lambda.sh` | `sam build` → `sam deploy` (reads `infra/deploy.env` or `samconfig.toml`) |
| `scripts/deploy_lambda.sh --with-gcp-auth` | Runs GCP setup, then deploy |
| `scripts/deploy_lambda.sh --guided` | Interactive first deploy |

Lambda loads the secret via `GCP_CREDENTIALS_SECRET_ARN` in `infra/template.yaml` (see `src/sarr/api/gcp_auth.py`). Laptop `gcloud auth application-default login` is **not** used in AWS.

## Config files

| File | Committed | Purpose |
|---|---|---|
| `infra/deploy.env.example` | yes | Template for deploy env vars |
| `infra/deploy.env` | no (gitignored) | Your Qdrant URL/key, ECR repo URI |
| `samconfig.toml.example` | yes | Template SAM deploy config |
| `samconfig.toml` | no (gitignored) | Written by `sam deploy --guided` |
| `infra/template.yaml` | yes | Lambda + API Gateway + IAM for Secrets Manager |

### `infra/deploy.env` variables

```bash
AWS_REGION=us-east-1
SAM_STACK=sarr-search
GCP_PROJECT_ID=sarr-505305          # Vertex billing project
GCP_SECRET_NAME=sarr-search/gcp-vertex
QDRANT_URL=https://….cloud.qdrant.io
QDRANT_API_KEY=…
QDRANT_COLLECTION=sarr
SAM_IMAGE_REPOSITORY=123….dkr.ecr.us-east-1.amazonaws.com/…   # after first guided deploy
```

After the first guided deploy, copy the **image repository** URI from the SAM prompt (or ECR console) into `SAM_IMAGE_REPOSITORY` so non-guided redeploys work without `samconfig.toml`.

## Manual SAM (alternative)

```bash
sam build --template infra/template.yaml --use-container
sam deploy --guided --template .aws-sam/build/template.yaml
```

When prompted:

| Parameter | Example |
|---|---|
| Stack name | `sarr-search` |
| Region | `us-east-1` |
| `QdrantUrl` | `https://….aws.cloud.qdrant.io` |
| `QdrantApiKey` | your key |
| `QdrantCollection` | `sarr` |
| `GcpProjectId` | `sarr-505305` |

Later: `make sam-deploy` or `sam deploy` (uses `samconfig.toml`).

## After deploy

SAM prints **ApiUrl**, e.g. `https://xxxx.execute-api.us-east-1.amazonaws.com/`.

```bash
export API_URL=https://xxxx.execute-api.us-east-1.amazonaws.com

curl -s "$API_URL/healthz"

curl -s "$API_URL/v1/search" \
  -H 'Content-Type: application/json' \
  -d '{"query":"machine learning","limit":5,"rerank":false}'
```

### Point frontend + CLI at Lambda

```bash
export SARR_API_URL="$API_URL"
sarr search "async HTTP client"
```

GitHub Pages demo (`.github/workflows/pages.yml`):

1. Repository **Settings → Secrets and variables → Actions → Variables**
2. Set `VITE_API_BASE_URL` to the `ApiUrl` (no trailing slash)
3. Enable Pages with **Source: GitHub Actions**
4. Push to `main` under `frontend/**` or run **Deploy frontend** workflow

Local frontend override: `frontend/.env` → `VITE_API_BASE_URL=…`

## What gets created

- **ECR** image from `docker/Dockerfile.lambda` (FastAPI + Mangum + ONNX + google-genai + boto3)
- **Lambda** function (container, ~3 GB RAM, 90s timeout)
- **HTTP API** (API Gateway) → `ANY /` and `ANY /{proxy+}`
- **Secrets Manager** secret `{stack}/gcp-vertex` (via setup script, not CloudFormation)
- **IAM** on Lambda: `secretsmanager:GetSecretValue` for that secret

Handler: `sarr.api.handler.handler` (Mangum → FastAPI).

## Expectations / limits

- **Cold start** loads ONNX: ~5 s without rerank, ~16 s with rerank. Warm search p50 ~18 ms / p95 ~65 ms (`rerank=false`); p50 ~172 ms / p95 ~257 ms (`rerank=true`). Measured 20 Aug 2026.
- Runtime image is **ONNX Runtime** (no PyTorch in the Lambda image).
- On Apple Silicon: SAM uses `--use-container` for `linux/amd64`.
- **RAG SSE** through API Gateway is best-effort; ranked list always works. Local RAG: `make run-api`.
- Do not commit Qdrant keys or GCP service account JSON files.

## Tear down

```bash
make sam-delete
# Optional: delete secret manually
aws secretsmanager delete-secret --secret-id sarr-search/gcp-vertex --force-delete-without-recovery --region us-east-1
```
