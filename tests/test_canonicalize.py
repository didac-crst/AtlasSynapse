"""Unit tests for deterministic content canonicalization."""

from __future__ import annotations

from semantic_memory.content.canonicalize import canonicalize_content, hash_content


def test_html_to_markdown_basic_structure() -> None:
    html = """
    <html><head><style>.x{}</style><script>evil()</script></head>
    <body>
      <h1>Hello</h1>
      <p>One <em>two</em> <strong>three</strong>.</p>
      <ul><li>Alpha</li><li>Beta</li></ul>
      <p>See <a href="https://example.test">link</a>.</p>
    </body></html>
    """
    result = canonicalize_content(html, "html", "markdown")
    md = result.canonical_content
    assert md == canonicalize_content(html, "html", "markdown").canonical_content
    assert "# Hello" in md
    assert "*two*" in md
    assert "**three**" in md
    assert "- Alpha" in md
    assert "[link](https://example.test)" in md
    assert "evil" not in md
    assert result.original_content == html
    assert result.original_hash == hash_content(html)
    assert result.metadata() == {"method": "html_to_markdown", "version": "1"}


def test_markdown_normalize_line_endings() -> None:
    raw = "# Title\r\n\r\nBody  \r\n"
    result = canonicalize_content(raw, "markdown", "markdown")
    assert result.original_content == raw
    assert result.canonical_content == "# Title\n\nBody\n"
    assert result.canonicalizer == "markdown_normalize"


def test_text_to_markdown_normalizes_only() -> None:
    raw = "plain text\nline two"
    result = canonicalize_content(raw, "text", "markdown")
    assert result.canonical_format == "markdown"
    assert result.canonical_content == "plain text\nline two\n"
    assert result.canonicalizer == "text_normalize"
