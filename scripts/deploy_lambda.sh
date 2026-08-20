#!/usr/bin/env bash
# Build and deploy the SARR Lambda stack (SAM). Optionally refresh GCP Vertex auth first.
#
# Usage:
#   cp infra/deploy.env.example infra/deploy.env   # fill Qdrant + ECR once
#   ./scripts/deploy_lambda.sh
#   ./scripts/deploy_lambda.sh --with-gcp-auth      # run setup_gcp_vertex_auth.sh first
#   ./scripts/deploy_lambda.sh --guided             # first-time stack (creates samconfig.toml)
#
# Requires: sam, docker, aws CLI. For --with-gcp-auth: gcloud.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_ENV="${DEPLOY_ENV:-$ROOT/infra/deploy.env}"
WITH_GCP_AUTH=0
GUIDED=0

usage() {
  cat <<'EOF'
Deploy SARR search API to AWS Lambda (SAM + container image).

  ./scripts/deploy_lambda.sh [--with-gcp-auth] [--guided]

Options:
  --with-gcp-auth   Run scripts/setup_gcp_vertex_auth.sh before deploy (Vertex Gemini on Lambda)
  --guided          sam deploy --guided (first deploy; writes samconfig.toml)

Config (optional): infra/deploy.env — copy from infra/deploy.env.example
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-gcp-auth) WITH_GCP_AUTH=1 ;;
    --guided) GUIDED=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

if [[ -f "$DEPLOY_ENV" ]]; then
  # shellcheck disable=SC1090
  set -a && source "$DEPLOY_ENV" && set +a
fi

AWS_REGION="${AWS_REGION:-us-east-1}"
SAM_STACK="${SAM_STACK:-sarr-search}"
SAM_TEMPLATE="${SAM_TEMPLATE:-infra/template.yaml}"
BUILT_TEMPLATE="${BUILT_TEMPLATE:-.aws-sam/build/template.yaml}"
GcpProjectId="${GcpProjectId:-${GCP_PROJECT_ID:-sarr-505305}}"

QDRANT_URL="${QDRANT_URL:-}"
QDRANT_COLLECTION="${QDRANT_COLLECTION:-sarr}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-/var/task/models/bge-small-en-v1.5}"
RERANKER_MODEL="${RERANKER_MODEL:-/var/task/models/ms-marco-MiniLM-L-6-v2}"
SAM_IMAGE_REPOSITORY="${SAM_IMAGE_REPOSITORY:-}"

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

command -v sam >/dev/null 2>&1 || die "SAM CLI not found. See infra/README.md."
command -v docker >/dev/null 2>&1 || die "Docker not running / not installed."

if [[ "$WITH_GCP_AUTH" -eq 1 ]]; then
  log "Refreshing GCP Vertex credentials in Secrets Manager…"
  bash "$ROOT/scripts/setup_gcp_vertex_auth.sh"
fi

log "Building Lambda container (linux/amd64 via SAM)…"
sam build --template "$SAM_TEMPLATE" --use-container

if [[ "$GUIDED" -eq 1 ]]; then
  log "Guided deploy (creates/updates samconfig.toml)…"
  sam deploy --guided --template "$BUILT_TEMPLATE"
  exit 0
fi

if [[ -f "$ROOT/samconfig.toml" ]]; then
  log "Deploying with samconfig.toml…"
  sam deploy
  exit 0
fi

[[ -n "$QDRANT_URL" ]] || die "Set QDRANT_URL in infra/deploy.env or run with --guided once."

PARAMS=(
  "QdrantUrl=$QDRANT_URL"
  "QdrantCollection=$QDRANT_COLLECTION"
  "EmbeddingModel=$EMBEDDING_MODEL"
  "RerankerModel=$RERANKER_MODEL"
  "GcpProjectId=$GcpProjectId"
)
if [[ -n "${QDRANT_API_KEY:-}" ]]; then
  PARAMS+=("QdrantApiKey=$QDRANT_API_KEY")
fi

DEPLOY_ARGS=(
  --template-file "$BUILT_TEMPLATE"
  --stack-name "$SAM_STACK"
  --region "$AWS_REGION"
  --capabilities CAPABILITY_IAM
  --resolve-s3
  --no-confirm-changeset
  --disable-rollback
  --parameter-overrides "${PARAMS[*]}"
)

if [[ -n "$SAM_IMAGE_REPOSITORY" ]]; then
  DEPLOY_ARGS+=(--image-repository "$SAM_IMAGE_REPOSITORY")
else
  log "SAM_IMAGE_REPOSITORY not set — trying stack ECR from CloudFormation…"
  REPO="$(aws cloudformation describe-stack-resources \
    --stack-name "$SAM_STACK" \
    --region "$AWS_REGION" \
    --query "StackResources[?ResourceType=='AWS::ECR::Repository'].PhysicalResourceId" \
    --output text 2>/dev/null || true)"
  if [[ -n "$REPO" && "$REPO" != "None" ]]; then
    ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
    SAM_IMAGE_REPOSITORY="${ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com/${REPO}"
    DEPLOY_ARGS+=(--image-repository "$SAM_IMAGE_REPOSITORY")
    log "Using image repository: $SAM_IMAGE_REPOSITORY"
  else
    die "First deploy needs --guided or SAM_IMAGE_REPOSITORY in infra/deploy.env"
  fi
fi

log "Deploying stack $SAM_STACK ($AWS_REGION)…"
sam deploy "${DEPLOY_ARGS[@]}"

log "Deploy complete. Fetch ApiUrl:"
aws cloudformation describe-stacks \
  --stack-name "$SAM_STACK" \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
  --output text
