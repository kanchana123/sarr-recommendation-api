#!/usr/bin/env bash
# Build the health collector Lambda zip for infra/terraform/collector.
#
#   ./scripts/build_collector_lambda.sh        # -> build/collector_lambda.zip
#
# Installs Linux x86_64 / CPython 3.12 wheels regardless of the build machine.
# Only sarr.common and sarr.collect are bundled; boto3 comes from the Lambda
# runtime. File times and order are fixed so unchanged code gives the same
# hash and Terraform doesn't redeploy.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/build/collector_lambda"
OUT="$ROOT/build/collector_lambda.zip"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON=python3

rm -rf "$BUILD" "$OUT"
mkdir -p "$BUILD/sarr"

"$PYTHON" -m pip install --quiet --disable-pip-version-check \
  --target "$BUILD" \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  --no-compile \
  "qdrant-client>=1.9" \
  "httpx>=0.27" \
  "pydantic>=2.6" \
  "pydantic-settings>=2.2"

cp "$ROOT/src/sarr/__init__.py" "$BUILD/sarr/"
cp -R "$ROOT/src/sarr/common" "$ROOT/src/sarr/collect" "$BUILD/sarr/"
find "$BUILD" -name __pycache__ -prune -exec rm -rf {} +
find "$BUILD" -exec touch -h -t 198001010000 {} +

(cd "$BUILD" && find . -type f | LC_ALL=C sort | zip -q -X -9 "$OUT" -@)

printf 'built %s (%s zipped, %s unzipped)\n' \
  "${OUT#"$ROOT"/}" "$(du -h "$OUT" | cut -f1)" "$(du -sh "$BUILD" | cut -f1)"
