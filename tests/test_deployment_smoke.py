"""Deployment packaging and entrypoint smoke tests (no live Docker required)."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_and_compose_define_app_and_migrate() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    # Default compose is portable local (bundled Postgres).
    compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    compose = yaml.safe_load(compose_text)
    entrypoint = (ROOT / "scripts" / "docker-entrypoint.sh").read_text(encoding="utf-8")
    smoke = (ROOT / "scripts" / "smoke_deploy.sh").read_text(encoding="utf-8")

    assert "FROM python:3.12" in dockerfile
    assert "alembic upgrade head" in entrypoint
    assert "semantic_memory.seeding" in entrypoint
    assert "semantic-memory-mcp" in entrypoint
    assert "HTTP_API_TOKEN is required" in smoke

    services = compose["services"]
    assert set(services) >= {"postgres", "migrate", "api", "mcp"}

    migrate_url = services["migrate"]["environment"]["DATABASE_URL"]
    api_url = services["api"]["environment"]["DATABASE_URL"]
    mcp_url = services["mcp"]["environment"]["DATABASE_URL"]
    assert "@postgres:5432/" in migrate_url
    assert migrate_url == api_url == mcp_url

    assert services["migrate"]["command"] == ["migrate"]
    assert services["api"]["command"] == ["api"]
    assert services["api"]["ports"] == ["${HTTP_PORT:-8000}:${HTTP_PORT:-8000}"]
    assert services["mcp"]["tty"] is False
    assert services["mcp"]["stdin_open"] is True
    assert "mcp" in services["mcp"].get("profiles", [])


def test_satellite_compose_uses_shared_database_network() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.satellite.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert set(services) >= {"migrate", "api", "mcp"}
    assert "postgres" not in services
    assert compose["networks"]["satellite-databases"]["external"] is True
    assert services["migrate"]["command"] == ["migrate"]
    assert services["api"]["command"] == ["api"]
    assert services["mcp"]["tty"] is False
    assert services["mcp"]["stdin_open"] is True


def test_deployment_docs_cover_backup_and_secrets() -> None:
    doc = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
    # Contract checks — avoid exact prose/command layout that docs rewording would break.
    assert "pg_dump" in doc
    assert '-U "$POSTGRES_USER"' in doc
    assert '-d "$POSTGRES_DB"' in doc
    assert "--format=custom" in doc
    assert any("postgres" in line and "host" in line.lower() for line in doc.splitlines())
    assert "/docs" in doc
    assert "HTTP_API_TOKEN" in doc
    assert "rate limiting" in doc.lower()
    assert "tls" in doc.lower()
    assert "reverse proxy" in doc.lower()
