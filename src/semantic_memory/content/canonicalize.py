"""Deterministic content canonicalization for source ingestion.

Callers submit original bytes/text; AtlasSynapse owns conversion so the same
input always yields the same canonical body and hashes regardless of client.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

ContentFormat = Literal["markdown", "text", "html"]

HTML_TO_MARKDOWN_METHOD = "html_to_markdown"
HTML_TO_MARKDOWN_VERSION = "1"
MARKDOWN_NORMALIZE_METHOD = "markdown_normalize"
MARKDOWN_NORMALIZE_VERSION = "1"
TEXT_NORMALIZE_METHOD = "text_normalize"
TEXT_NORMALIZE_VERSION = "1"

_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "div",
        "dl",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "ul",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
)


@dataclass(frozen=True, slots=True)
class CanonicalizationResult:
    canonical_content: str
    canonical_format: ContentFormat
    original_content: str
    original_format: ContentFormat
    original_hash: str
    canonical_hash: str
    canonicalizer: str
    canonicalizer_version: str

    def metadata(self) -> dict[str, str]:
        return {
            "method": self.canonicalizer,
            "version": self.canonicalizer_version,
        }


def hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def canonicalize_content(
    content: str,
    source_format: ContentFormat,
    target_format: ContentFormat = "markdown",
) -> CanonicalizationResult:
    """Preserve exact original content and produce a deterministic canonical body."""
    if not content:
        raise ValueError("content must not be empty")

    original = content  # exact bytes identity as Unicode string; do not mutate
    original_hash = hash_content(original)

    if source_format == "html" and target_format == "markdown":
        canonical = _html_to_markdown(original)
        method, version = HTML_TO_MARKDOWN_METHOD, HTML_TO_MARKDOWN_VERSION
    elif source_format == "markdown" and target_format == "markdown":
        canonical = _normalize_text_body(original)
        method, version = MARKDOWN_NORMALIZE_METHOD, MARKDOWN_NORMALIZE_VERSION
    elif source_format == "text" and target_format in ("text", "markdown"):
        canonical = _normalize_text_body(original)
        method, version = TEXT_NORMALIZE_METHOD, TEXT_NORMALIZE_VERSION
    elif source_format == target_format:
        canonical = _normalize_text_body(original)
        method, version = f"{source_format}_normalize", "1"
    else:
        raise ValueError(f"Unsupported canonicalization {source_format!r} → {target_format!r}")

    return CanonicalizationResult(
        canonical_content=canonical,
        canonical_format=target_format,
        original_content=original,
        original_format=source_format,
        original_hash=original_hash,
        canonical_hash=hash_content(canonical),
        canonicalizer=method,
        canonicalizer_version=version,
    )


def _normalize_text_body(content: str) -> str:
    """Trivial deterministic normalization: CRLF→LF, strip trailing spaces per line."""
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip(" \t") for line in text.split("\n")]
    # Trim trailing blank lines; keep a single trailing newline for non-empty body.
    while lines and lines[-1] == "":
        lines.pop()
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _html_to_markdown(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    root = soup.body if soup.body is not None else soup
    parts: list[str] = []
    _render_nodes(root.children, parts, list_depth=0)
    text = "".join(parts)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip("\n")
    if not text:
        return ""
    return text + "\n"


def _render_nodes(nodes: Iterable[Any], parts: list[str], *, list_depth: int) -> None:
    for node in nodes:
        _render_node(node, parts, list_depth=list_depth)


def _render_node(node: object, parts: list[str], *, list_depth: int) -> None:
    if isinstance(node, NavigableString):
        text = str(node)
        if not text:
            return
        parent_name = node.parent.name if isinstance(node.parent, Tag) else None
        if parent_name in {"script", "style"}:
            return
        if parent_name in {"pre", "code"}:
            parts.append(text)
        else:
            parts.append(re.sub(r"[ \t\r\n\f\v]+", " ", text))
        return

    if not isinstance(node, Tag):
        return

    name = (node.name or "").lower()
    if name in {"script", "style", "noscript", "meta", "link", "head"}:
        return
    if name == "br":
        parts.append("\n")
        return
    if name == "hr":
        _ensure_blank_line(parts)
        parts.append("---\n\n")
        return
    if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = int(name[1])
        inner = _inline_markdown(node)
        _ensure_blank_line(parts)
        parts.append(f"{'#' * level} {inner}\n\n")
        return
    if name == "p":
        inner = _inline_markdown(node)
        if inner.strip():
            _ensure_blank_line(parts)
            parts.append(f"{inner.strip()}\n\n")
        return
    if name == "blockquote":
        inner_parts: list[str] = []
        _render_nodes(node.children, inner_parts, list_depth=list_depth)
        quoted = "".join(inner_parts).strip("\n")
        if quoted:
            _ensure_blank_line(parts)
            for line in quoted.split("\n"):
                parts.append(f"> {line}\n")
            parts.append("\n")
        return
    if name == "pre":
        code = node.get_text()
        _ensure_blank_line(parts)
        parts.append(f"```\n{code.rstrip(chr(10))}\n```\n\n")
        return
    if name in {"ul", "ol"}:
        _ensure_blank_line(parts)
        index = 1
        for child in node.children:
            if not isinstance(child, Tag) or (child.name or "").lower() != "li":
                continue
            marker = f"{index}." if name == "ol" else "-"
            index += 1
            indent = "  " * list_depth
            item_parts: list[str] = []
            _render_nodes(child.children, item_parts, list_depth=list_depth + 1)
            item_text = "".join(item_parts).strip()
            item_text = re.sub(r"\n+", "\n", item_text)
            lines = item_text.split("\n") if item_text else [""]
            parts.append(f"{indent}{marker} {lines[0]}\n")
            for cont in lines[1:]:
                parts.append(f"{indent}  {cont}\n")
        parts.append("\n")
        return
    if name == "li":
        inner = _inline_markdown(node)
        parts.append(f"- {inner.strip()}\n")
        return
    if name == "a":
        parts.append(_format_link(node))
        return
    if name in {"strong", "b"}:
        parts.append(f"**{_inline_markdown(node)}**")
        return
    if name in {"em", "i"}:
        parts.append(f"*{_inline_markdown(node)}*")
        return
    if name == "code":
        code = node.get_text()
        parts.append(f"`{code.replace('`', '\\`')}`")
        return
    if name in {"table", "thead", "tbody", "tr", "td", "th"}:
        if name == "tr":
            cells = []
            for child in node.children:
                if isinstance(child, Tag) and (child.name or "").lower() in {"td", "th"}:
                    cells.append(_inline_markdown(child).strip())
            if cells:
                parts.append(" | ".join(cells) + "\n")
            return
        _render_nodes(node.children, parts, list_depth=list_depth)
        if name == "table":
            parts.append("\n")
        return

    before = len(parts)
    _render_nodes(node.children, parts, list_depth=list_depth)
    if name in _BLOCK_TAGS and any(parts[before:]):
        _ensure_blank_line(parts)


def _inline_markdown(node: Tag) -> str:
    parts: list[str] = []
    _render_nodes(node.children, parts, list_depth=0)
    text = "".join(parts)
    text = re.sub(r"[ \t\r\n\f\v]+", " ", text)
    return text.strip()


def _format_link(node: Tag) -> str:
    label = _inline_markdown(node)
    href = node.get("href")
    if isinstance(href, list):
        href = href[0] if href else None
    if not href:
        return label
    return f"[{label}]({href})"


def _ensure_blank_line(parts: list[str]) -> None:
    if not parts:
        return
    trailing = ""
    for chunk in reversed(parts):
        trailing = chunk + trailing
        if len(trailing) >= 2:
            break
    if trailing.endswith("\n\n"):
        return
    if trailing.endswith("\n"):
        parts.append("\n")
        return
    parts.append("\n\n")
