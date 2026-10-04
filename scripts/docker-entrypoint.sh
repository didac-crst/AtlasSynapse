#!/bin/sh
set -eu

cmd="${1:-api}"
shift || true

case "$cmd" in
  api)
    exec uvicorn semantic_memory.api.app:create_app --factory \
      --host "${HTTP_HOST:-0.0.0.0}" \
      --port "${HTTP_PORT:-8000}"
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  mcp)
    exec semantic-memory-mcp
    ;;
  *)
    exec "$cmd" "$@"
    ;;
esac
