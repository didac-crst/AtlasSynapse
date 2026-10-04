#!/usr/bin/env sh
# Smoke-check a running AtlasSynapse HTTP deployment.
# Requires HTTP_API_TOKEN so the authentication boundary is always exercised.
set -eu

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
HTTP_API_TOKEN="${HTTP_API_TOKEN:-}"

if [ -z "${HTTP_API_TOKEN}" ]; then
  echo "HTTP_API_TOKEN is required for deployment smoke checks" >&2
  exit 1
fi

echo "Checking live: ${BASE_URL}/health/live"
curl -fsS "${BASE_URL}/health/live" | grep -q '"status"'

echo "Checking ready: ${BASE_URL}/health/ready"
curl -fsS "${BASE_URL}/health/ready" | grep -q '"status"'

echo "Checking authenticated denial without token (expect 401)"
code=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/v1/ontology/classes?class_key=Person" || true)
if [ "$code" != "401" ]; then
  echo "expected 401 without token, got ${code}" >&2
  exit 1
fi

echo "Checking authenticated ontology read"
curl -fsS \
  -H "Authorization: Bearer ${HTTP_API_TOKEN}" \
  "${BASE_URL}/v1/ontology/classes?class_key=Person" | grep -q '"key"'

echo "Smoke checks passed."
