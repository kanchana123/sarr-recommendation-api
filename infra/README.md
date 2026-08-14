# Deploy SARR to AWS Lambda (container image)

## Prerequisites

- AWS account + IAM user/role that can use Lambda, ECR, API Gateway, CloudFormation
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured (`aws configure`)
- [SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
- Docker running (SAM builds the Lambda image locally)
- Qdrant Cloud URL + API key + collection (`sarr`)

## Deploy (SAM)

From the **repo root**:

```bash
# 1) Build the Lambda container image from docker/Dockerfile.lambda
sam build \
  --template infra/template.yaml \
  --use-container

# 2) First-time guided deploy (creates stack + ECR repo)
sam deploy --guided \
  --template .aws-sam/build/template.yaml
```

When prompted, set:

| Parameter | Example |
|---|---|
| Stack name | `sarr-search` |
| Region | `us-east-1` (match your Qdrant region if possible) |
| `QdrantUrl` | `https://….aws.cloud.qdrant.io` |
| `QdrantApiKey` | your key |
| `QdrantCollection` | `sarr` |
| Confirm changes / allow SAM IAM roles | Yes |

Later deploys (non-guided):

```bash
sam build --template infra/template.yaml --use-container
sam deploy
```

## After deploy

SAM prints **ApiUrl**, e.g. `https://xxxx.execute-api.us-east-1.amazonaws.com/`.

```bash
curl -s "$API_URL/healthz"

curl -s "$API_URL/v1/search" \
  -H 'Content-Type: application/json' \
  -d '{"query":"machine learning","limit":5,"rerank":false}'
```

Point the CLI / frontend at that host:

```bash
export SARR_API_URL="https://xxxx.execute-api.us-east-1.amazonaws.com"
sarr search "async HTTP client"

# frontend/.env
VITE_API_BASE_URL=https://xxxx.execute-api.us-east-1.amazonaws.com
```

## What gets created

- **ECR** image from `docker/Dockerfile.lambda` (FastAPI + Mangum + models deps)
- **Lambda** function (container, ~3 GB RAM, 60s timeout)
- **HTTP API** (API Gateway) → `ANY /` and `ANY /{proxy+}`

Handler: `sarr.api.handler.handler` (Mangum → FastAPI).

## Expectation / limits

- **Cold start is slow** (loads embedding weights into memory). First request after idle can take many seconds; warm requests are much faster.
- Image is **large** (PyTorch + sentence-transformers). Stay under Lambda’s container image size limit; build on `linux/amd64` if you’re on Apple Silicon:

```bash
docker build --platform linux/amd64 -f docker/Dockerfile.lambda -t sarr-search-api .
```

- Optional: enable **provisioned concurrency** in the Lambda console if you need steadier demo latency.
- Do not commit Qdrant keys; pass them as SAM parameters / SSM later.

## Tear down

```bash
sam delete --stack-name sarr-search
```
