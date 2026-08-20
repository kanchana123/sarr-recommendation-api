#!/usr/bin/env bash
# Provision a GCP service account for Vertex Gemini and store its JSON key in
# AWS Secrets Manager for the Lambda stack. Safe to re-run (updates secret value).
#
# Usage:
#   ./scripts/setup_gcp_vertex_auth.sh
#   GCP_PROJECT_ID=sarr-505305 SAM_STACK=sarr-search ./scripts/setup_gcp_vertex_auth.sh
#
# Requires: gcloud (logged in), aws CLI (configured), jq (optional, for checks)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_ENV="${DEPLOY_ENV:-$ROOT/infra/deploy.env}"

if [[ -f "$DEPLOY_ENV" ]]; then
  # shellcheck disable=SC1090
  set -a && source "$DEPLOY_ENV" && set +a
fi

GCP_PROJECT_ID="${GCP_PROJECT_ID:-sarr-505305}"
GCP_SA_NAME="${GCP_SA_NAME:-sarr-lambda-vertex}"
GCP_SA_DISPLAY_NAME="${GCP_SA_DISPLAY_NAME:-SARR Lambda Vertex}"
AWS_REGION="${AWS_REGION:-us-east-1}"
SAM_STACK="${SAM_STACK:-sarr-search}"
GCP_SECRET_NAME="${GCP_SECRET_NAME:-${SAM_STACK}/gcp-vertex}"
GCP_VERTEX_API="${GCP_VERTEX_API:-aiplatform.googleapis.com}"

SA_EMAIL="${GCP_SA_NAME}@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
TMP_KEY="$(mktemp "${TMPDIR:-/tmp}/sarr-lambda-vertex.XXXXXX.json")"
trap 'rm -f "$TMP_KEY"' EXIT

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

command -v gcloud >/dev/null 2>&1 || die "gcloud not found. Install Google Cloud SDK."
command -v aws >/dev/null 2>&1 || die "aws CLI not found."

log "GCP project: $GCP_PROJECT_ID"
log "Service account: $SA_EMAIL"
log "Secrets Manager secret: $GCP_SECRET_NAME ($AWS_REGION)"

log "Enabling Vertex AI API (if needed)…"
gcloud services enable "$GCP_VERTEX_API" --project="$GCP_PROJECT_ID" >/dev/null

if gcloud iam service-accounts describe "$SA_EMAIL" --project="$GCP_PROJECT_ID" >/dev/null 2>&1; then
  log "Service account already exists."
else
  log "Creating service account…"
  gcloud iam service-accounts create "$GCP_SA_NAME" \
    --project="$GCP_PROJECT_ID" \
    --display-name="$GCP_SA_DISPLAY_NAME"
fi

for role in roles/aiplatform.user roles/serviceusage.serviceUsageConsumer; do
  log "Granting $role…"
  gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" \
    --role="$role" \
    --quiet >/dev/null
done

log "Creating a new service account key (stored only in Secrets Manager)…"
gcloud iam service-accounts keys create "$TMP_KEY" \
  --iam-account="$SA_EMAIL" \
  --project="$GCP_PROJECT_ID" >/dev/null
chmod 600 "$TMP_KEY"

if ! python3 -c "import json; d=json.load(open('$TMP_KEY')); assert d.get('type')=='service_account'" 2>/dev/null; then
  die "Generated key is not valid service account JSON."
fi

if aws secretsmanager describe-secret --secret-id "$GCP_SECRET_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
  log "Updating existing secret…"
  aws secretsmanager put-secret-value \
    --region "$AWS_REGION" \
    --secret-id "$GCP_SECRET_NAME" \
    --secret-string "file://$TMP_KEY" >/dev/null
else
  log "Creating secret…"
  aws secretsmanager create-secret \
    --region "$AWS_REGION" \
    --name "$GCP_SECRET_NAME" \
    --description "GCP service account for SARR Lambda Vertex Gemini" \
    --secret-string "file://$TMP_KEY" >/dev/null
fi

SECRET_ARN="$(aws secretsmanager describe-secret \
  --secret-id "$GCP_SECRET_NAME" \
  --region "$AWS_REGION" \
  --query ARN \
  --output text)"

log "Done."
log "Secret ARN: $SECRET_ARN"
log "Lambda reads this via GCP_CREDENTIALS_SECRET_ARN=$GCP_SECRET_NAME (set in infra/template.yaml)."
log "Next: make deploy-lambda   (or ./scripts/deploy_lambda.sh)"
