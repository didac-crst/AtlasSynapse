"""Configuration and package smoke tests."""

from __future__ import annotations

from semantic_memory.config import Settings, get_settings
from semantic_memory.mcp import MCPPlaceholder
from semantic_memory.observability.logging import JsonFormatter, configure_logging


def test_settings_load_from_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    get_settings.cache_clear()
    monkeypatch.setenv("APP_NAME", "AtlasSynapseTest")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://semantic_memory:semantic_memory@localhost:5432/semantic_memory",
    )
    settings = Settings()
    assert settings.app_name == "AtlasSynapseTest"
    assert settings.log_level == "DEBUG"
    assert "postgresql+psycopg://" in settings.sqlalchemy_database_uri
    get_settings.cache_clear()


def test_mcp_placeholder() -> None:
    placeholder = MCPPlaceholder.from_settings(Settings())
    assert placeholder.ready is True
    assert "create_entity" in placeholder.tools
    assert "assert_statement" in placeholder.tools
    assert "explain_statement" in placeholder.tools
    assert "add_evidence" in placeholder.tools
    assert "get_timeline" in placeholder.tools
    assert "find_conflicts" in placeholder.tools
    assert "merge_entity" in placeholder.tools
    assert "assert_batch" in placeholder.tools
    assert "get_class" in placeholder.tools
    assert "get_predicate" in placeholder.tools
    assert "search_ontology" in placeholder.tools
    assert "get_ontology_context" in placeholder.tools
    assert placeholder.transport in {"stdio", "http"}


def test_json_logging_formatter() -> None:
    configure_logging("INFO")
    formatter = JsonFormatter()
    import logging

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    payload = formatter.format(record)
    assert '"message": "hello"' in payload
    assert '"level": "INFO"' in payload
