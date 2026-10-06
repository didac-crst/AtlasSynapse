# Deployment and operations readiness

This document covers packaging, secrets, migrations, authentication, MCP transport, backups, and the operational gaps that remain reverse-proxy concerns.

## Compose packaging

`docker-compose.yml` runs the portable local stack:

1. `postgres` — PostgreSQL 16
2. `migrate` — one-shot `alembic upgrade head`
3. `api` — uvicorn HTTP service (depends on a successful migrate)
4. `mcp` (profile `mcp`) — optional stdio MCP process for local agent wiring

On Satellite, use `docker compose -f docker-compose.satellite.yml` instead (shared
Postgres network; no bundled database).

```bash
cp .env.example .env
# edit secrets (especially POSTGRES_PASSWORD, HTTP_API_TOKEN, ADMIN_API_TOKEN)
docker compose up --build
curl -fsS http://127.0.0.1:8000/health/live
curl -fsS http://127.0.0.1:8000/health/ready
```

Compose `migrate` / `api` / `mcp` always build `DATABASE_URL` from `POSTGRES_*` with hostname `postgres`. Host-side `DATABASE_URL` (usually `localhost`) is only for local tools such as pytest against the published Postgres port.

Migrations are a separate Compose service by default. Do not rely on the API process to mutate schema on every start.

## Secrets and configuration

| Variable | Purpose |
| --- | --- |
| `POSTGRES_*` | Compose Postgres credentials and the in-network DB URL for app containers |
| `DATABASE_URL` | Host-side SQLAlchemy URL (`localhost`) for local tools |
| `HTTP_API_TOKEN` | Bearer / `X-API-Token` for protected HTTP routes |
| `ADMIN_API_TOKEN` | `X-Admin-Token` for actor provisioning |
| `APP_ENV` | `development` or `production` |
| `RAW_PAYLOAD_RETENTION` | `none` / `redacted` / `full` |

Production (`APP_ENV=production`) fails closed unless:

- `HTTP_API_TOKEN` is set
- `ADMIN_API_TOKEN` is set
- database credentials are not the development `semantic_memory:semantic_memory` pair

Store secrets in the platform secret manager (Compose `.env` for local only, never commit real values). Rotate API and admin tokens independently.

## HTTP authentication

- Health routes (`/health` and `/health/*`) remain unauthenticated for probes.
- OpenAPI documentation routes (`/docs`, `/openapi.json`, `/redoc`) are also public when enabled. In production the app disables `/docs` / `/redoc`; prefer keeping the API behind a private network or reverse proxy regardless.
- All other application routes require `Authorization: Bearer <HTTP_API_TOKEN>` or `X-API-Token` when auth is enforced.
- Actor provisioning additionally requires `X-Admin-Token: <ADMIN_API_TOKEN>`.
- Request bodies still carry `actor_key` for capability and audit attribution; that key is not an authentication secret.

In non-production, HTTP API auth is enforced only when `HTTP_API_TOKEN` is non-empty so local tests can stay unauthenticated unless configured. Production always requires a non-empty token.

## MCP transport

```bash
semantic-memory-mcp
# or (pipe-based stdio; do not allocate a TTY)
docker compose --profile mcp run --rm -T mcp
```

The stdio server speaks MCP 2024-11-05 newline-delimited JSON-RPC, registers the typed tools from `docs/mcp-contract.md`, and delegates to services. It does not expose SQL, DDL, or hard deletes. HTTP MCP transport remains unimplemented; set `MCP_TRANSPORT=stdio`.

## Backup and restore

Logical backup (credentials come from the container environment, not the host shell):

```bash
docker compose exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > backup-$(date -u +%Y%m%dT%H%M%SZ).dump
```

Restore into an empty database (destructive — verify target first):

```bash
docker compose exec -T postgres \
  sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' \
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

After bring-up (`HTTP_API_TOKEN` is required):

```bash
HTTP_API_TOKEN=dev-api-token ./scripts/smoke_deploy.sh
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

The Compose defaults still allow local bootstrapping with development database credentials. Replace `POSTGRES_PASSWORD`, `HTTP_API_TOKEN`, and `ADMIN_API_TOKEN` before any shared environment. Production process startup refuses the development DB credential pair.
