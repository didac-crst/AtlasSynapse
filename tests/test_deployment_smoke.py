"""Deployment packaging and entrypoint smoke tests (no live Docker required)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_and_compose_define_app_and_migrate() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    entrypoint = (ROOT / "scripts" / "docker-entrypoint.sh").read_text(encoding="utf-8")
    smoke = (ROOT / "scripts" / "smoke_deploy.sh").read_text(encoding="utf-8")

    assert "FROM python:3.12" in dockerfile
    assert "migrate" in compose
    assert 'command: ["migrate"]' in compose
    assert "api:" in compose
    assert "@postgres:5432/" in compose
    assert "tty: false" in compose
    assert '"${HTTP_PORT:-8000}:${HTTP_PORT:-8000}"' in compose
    assert "alembic upgrade head" in entrypoint
    assert "semantic-memory-mcp" in entrypoint
    assert "HTTP_API_TOKEN is required" in smoke


def test_deployment_docs_cover_backup_and_secrets() -> None:
    doc = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
    assert "pg_dump" in doc
    assert 'sh -c \'pg_dump -U "$POSTGRES_USER"' in doc or 'pg_dump -U "$POSTGRES_USER"' in doc
    assert "hostname `postgres`" in doc or 'hostname "postgres"' in doc
    assert "/docs" in doc
    assert "HTTP_API_TOKEN" in doc
    assert "reverse proxy" in doc.lower() or "TLS" in doc
    assert "rate limiting" in doc.lower()
