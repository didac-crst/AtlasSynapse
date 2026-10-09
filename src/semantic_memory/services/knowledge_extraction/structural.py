"""Deterministic YAML/JSON structural parser.

Produces addressable fragments with paths and optional line locators.
Does not infer epistemic semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from semantic_memory.exceptions import UnsupportedSourceFormatError
from semantic_memory.services.knowledge_extraction.formats import (
    KnowledgePackageFormat,
    load_json_document,
)


@dataclass(frozen=True, slots=True)
class SourceFragment:
    """One addressable location in the source tree."""

    source_context_path: tuple[str | int, ...]
    raw_value: Any
    structural_type: str
    start_line: int | None = None
    end_line: int | None = None
    parent_key: str | None = None

    def path_list(self) -> list[str | int]:
        return list(self.source_context_path)

    def locator(self) -> dict[str, Any]:
        loc: dict[str, Any] = {
            "path": self.path_list(),
            "structural_type": self.structural_type,
        }
        if self.start_line is not None:
            loc["start_line"] = self.start_line
        if self.end_line is not None:
            loc["end_line"] = self.end_line
        if self.parent_key is not None:
            loc["parent_key"] = self.parent_key
        return loc


@dataclass
class StructuralParseResult:
    format: KnowledgePackageFormat
    root: Any
    fragments: list[SourceFragment] = field(default_factory=list)


def parse_structured_source(
    content: str, *, package_format: KnowledgePackageFormat
) -> StructuralParseResult:
    if package_format == KnowledgePackageFormat.JSON:
        root = load_json_document(content)
        fragments = _walk_value(root, path=(), parent_key=None, marks={})
        return StructuralParseResult(format=package_format, root=root, fragments=fragments)

    try:
        root = yaml.safe_load(content)
        composed = yaml.compose(content, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise UnsupportedSourceFormatError(
            "Content detected as YAML but failed to parse",
            details={"error": str(exc)},
        ) from exc
    if root is None:
        raise UnsupportedSourceFormatError("YAML document is empty", details={})

    marks = _collect_marks(composed) if composed is not None else {}
    fragments = _walk_value(root, path=(), parent_key=None, marks=marks)
    return StructuralParseResult(format=package_format, root=root, fragments=fragments)


def _collect_marks(node: Node | None) -> dict[tuple[str | int, ...], tuple[int, int]]:
    marks: dict[tuple[str | int, ...], tuple[int, int]] = {}
    if node is None:
        return marks

    def visit(current: Node, path: tuple[str | int, ...]) -> None:
        start = current.start_mark.line + 1
        end = current.end_mark.line + 1
        marks[path] = (start, end)
        if isinstance(current, MappingNode):
            for key_node, value_node in current.value:
                key = _scalar_key(key_node)
                visit(value_node, (*path, key))
        elif isinstance(current, SequenceNode):
            for index, child in enumerate(current.value):
                visit(child, (*path, index))

    visit(node, ())
    return marks


def _scalar_key(node: ScalarNode) -> str:
    # Keys in package YAML are expected to be plain strings.
    return str(node.value)


def _walk_value(
    value: Any,
    *,
    path: tuple[str | int, ...],
    parent_key: str | None,
    marks: dict[tuple[str | int, ...], tuple[int, int]],
) -> list[SourceFragment]:
    out: list[SourceFragment] = []
    start_line: int | None = None
    end_line: int | None = None
    if path in marks:
        start_line, end_line = marks[path]

    if isinstance(value, dict):
        if path:
            out.append(
                SourceFragment(
                    source_context_path=path,
                    raw_value=value,
                    structural_type="mapping",
                    start_line=start_line,
                    end_line=end_line,
                    parent_key=parent_key,
                )
            )
        for key, child in value.items():
            key_str = str(key)
            out.extend(
                _walk_value(
                    child,
                    path=(*path, key_str),
                    parent_key=key_str,
                    marks=marks,
                )
            )
        return out

    if isinstance(value, list):
        if path:
            out.append(
                SourceFragment(
                    source_context_path=path,
                    raw_value=value,
                    structural_type="sequence",
                    start_line=start_line,
                    end_line=end_line,
                    parent_key=parent_key,
                )
            )
        for index, child in enumerate(value):
            out.extend(
                _walk_value(
                    child,
                    path=(*path, index),
                    parent_key=parent_key,
                    marks=marks,
                )
            )
        return out

    out.append(
        SourceFragment(
            source_context_path=path,
            raw_value=value,
            structural_type=_scalar_type(value),
            start_line=start_line,
            end_line=end_line,
            parent_key=parent_key,
        )
    )
    return out


def _scalar_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__
