#!/usr/bin/env sh
# Smoke-check a running AtlasSynapse HTTP deployment.
set -eu

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
HTTP_API_TOKEN="${HTTP_API_TOKEN:-}"

echo "Checking live: ${BASE_URL}/health/live"
curl -fsS "${BASE_URL}/health/live" | grep -q '"status"'

echo "Checking ready: ${BASE_URL}/health/ready"
curl -fsS "${BASE_URL}/health/ready" | grep -q '"status"'

if [ -n "${HTTP_API_TOKEN}" ]; then
  echo "Checking authenticated OpenAPI denial without token (expect 401)"
  code=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/v1/ontology/classes?class_key=Person" || true)
  if [ "$code" != "401" ]; then
    echo "expected 401 without token, got ${code}" >&2
    exit 1
  fi
  echo "Checking authenticated ontology read"
  curl -fsS \
    -H "Authorization: Bearer ${HTTP_API_TOKEN}" \
    "${BASE_URL}/v1/ontology/classes?class_key=Person" | grep -q '"key"'
fi

echo "Smoke checks passed."
