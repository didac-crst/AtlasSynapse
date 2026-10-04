"""Deployment packaging and entrypoint smoke tests (no live Docker required)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_and_compose_define_app_and_migrate() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    entrypoint = (ROOT / "scripts" / "docker-entrypoint.sh").read_text(encoding="utf-8")

    assert "FROM python:3.12" in dockerfile
    assert "migrate" in compose
    assert 'command: ["migrate"]' in compose or 'command: ["migrate"]' in compose
    assert "api:" in compose
    assert "alembic upgrade head" in entrypoint
    assert "semantic-memory-mcp" in entrypoint


def test_deployment_docs_cover_backup_and_secrets() -> None:
    doc = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
    assert "pg_dump" in doc
    assert "HTTP_API_TOKEN" in doc
    assert "reverse proxy" in doc.lower() or "TLS" in doc
    assert "rate limiting" in doc.lower()
