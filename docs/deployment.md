# Deployment and operations readiness

This document covers packaging, secrets, migrations, authentication, MCP transport, backups, and the operational gaps that remain reverse-proxy concerns.

## Compose packaging

`docker-compose.yml` runs:

1. `postgres` — PostgreSQL 16
2. `migrate` — one-shot `alembic upgrade head`
3. `api` — uvicorn HTTP service (depends on a successful migrate)
4. `mcp` (profile `mcp`) — optional stdio MCP process for local agent wiring

```bash
cp .env.example .env
# edit secrets
docker compose up --build
curl -fsS http://127.0.0.1:8000/health/live
curl -fsS http://127.0.0.1:8000/health/ready
```

Migrations are a separate Compose service by default. Do not rely on the API process to mutate schema on every start.

## Secrets and configuration

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy PostgreSQL URL |
| `HTTP_API_TOKEN` | Bearer / `X-API-Token` for non-health HTTP routes |
| `ADMIN_API_TOKEN` | `X-Admin-Token` for actor provisioning |
| `APP_ENV` | `development` or `production` |
| `RAW_PAYLOAD_RETENTION` | `none` / `redacted` / `full` |

Production (`APP_ENV=production`) fails closed unless:

- `HTTP_API_TOKEN` is set
- `ADMIN_API_TOKEN` is set
- `DATABASE_URL` does not use the development `semantic_memory:semantic_memory` credentials

Store secrets in the platform secret manager (Compose `.env` for local only, never commit real values). Rotate API and admin tokens independently.

## HTTP authentication

- Health routes (`/health/*`) remain unauthenticated for probes.
- All other application routes require `Authorization: Bearer <HTTP_API_TOKEN>` or `X-API-Token`.
- Actor provisioning additionally requires `X-Admin-Token: <ADMIN_API_TOKEN>`.
- Request bodies still carry `actor_key` for capability and audit attribution; that key is not an authentication secret.

In non-production, HTTP API auth is enforced only when `HTTP_API_TOKEN` is non-empty so local tests can stay unauthenticated unless configured.

## MCP transport

```bash
semantic-memory-mcp
# or
docker compose --profile mcp run --rm mcp
```

The stdio server registers the typed tools from `docs/mcp-contract.md` and delegates to services. It does not expose SQL, DDL, or hard deletes. HTTP MCP transport remains unimplemented; set `MCP_TRANSPORT=stdio`.

## Backup and restore

Logical backup (example):

```bash
docker compose exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom \
  > backup-$(date -u +%Y%m%dT%H%M%SZ).dump
```

Restore into an empty database (destructive — verify target first):

```bash
docker compose exec -T postgres \
  pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists \
  < backup-YYYYMMDDTHHMMSSZ.dump
```

Retain dumps off-host. After restore, confirm `/health/ready` and that `alembic current` matches the expected revision. Point-in-time recovery requires PostgreSQL WAL archiving configured outside this repository.

## TLS, reverse proxy, and rate limiting

Terminate TLS at a reverse proxy (Caddy, nginx, Traefik, or a cloud load balancer). Recommended proxy responsibilities:

- TLS certificates and HSTS
- Request body size limits
- Rate limiting / abuse controls per client IP or API token
- Forwarding only to the Compose `api` service on the private network

AtlasSynapse does not currently implement in-process rate limiting.

## Production observability

- Application logs are JSON on stdout; scrape them with the platform log agent.
- Probe `/health/live` for liveness and `/health/ready` for DB/migration readiness.
- Mutation audit lives in `operation_log`; provider-call telemetry lives in `llm_call_log`.
- Metrics/tracing exporters are not bundled yet; prefer proxy and infrastructure metrics until an OpenTelemetry slice is added.

## Deployment smoke checks

After bring-up:

```bash
./scripts/smoke_deploy.sh
```

Or run `pytest tests/test_deployment_smoke.py tests/test_auth.py tests/test_mcp_server.py`.

## Reproducible installs

This repository commits `uv.lock`. Prefer:

```bash
uv sync --frozen
# or
pip install .
```

## Compose credentials

The Compose defaults still allow local bootstrapping with development database credentials. Replace `POSTGRES_PASSWORD`, `DATABASE_URL`, `HTTP_API_TOKEN`, and `ADMIN_API_TOKEN` before any shared environment. Production process startup refuses the development DB credential pair.
