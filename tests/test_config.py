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


def test_mcp_server_info() -> None:
    info = MCPPlaceholder.from_settings(Settings())
    assert info.ready is True
    assert "create_entity" in info.tools
    assert "assert_statement" in info.tools
    assert "explain_statement" in info.tools
    assert "add_evidence" in info.tools
    assert "get_timeline" in info.tools
    assert "find_conflicts" in info.tools
    assert "merge_entity" in info.tools
    assert "assert_batch" in info.tools
    assert "get_class" in info.tools
    assert "get_predicate" in info.tools
    assert "search_ontology" in info.tools
    assert "get_ontology_context" in info.tools
    assert "propose_class" in info.tools
    assert "get_proposal" in info.tools
    assert "apply_proposal" not in info.tools
    assert "search_entities" in info.tools
    assert "search_statements" in info.tools
    assert "search_semantic_memory" in info.tools
    assert "get_relevant_context" in info.tools
    assert "get_entity_neighborhood" in info.tools
    assert info.transport in {"stdio", "http"}


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
