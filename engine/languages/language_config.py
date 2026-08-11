"""
:class:`LanguageConfig` — per-language parser-shape data.

The :class:`~engine.ast_parser.ASTParser` contains **no** language-specific
``if/elif`` logic. All branching happens through these configs plus the
declarative ``.scm`` query files in :mod:`engine.queries`.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LanguageConfig:
    """Per-language metadata driving :class:`~engine.ast_parser.ASTParser`.

    ``symbol_node_types`` maps a tree-sitter node type to Constellation's
    canonical symbol kind. For a captured ``@sym.def`` node, the parser looks
    up ``symbol_node_types[node.type]`` to learn what kind of symbol it is —
    so the same generic extraction code serves every language.
    """

    # tree-sitter node type → canonical symbol kind ("class", "method", …)
    symbol_node_types: dict[str, str]

    # node types that carry a method/function body ("block" for Java).
    body_node_type: str = "block"

    # How a method's owning type is determined:
    #   "nesting" — walk up the AST; owning types are in ``parent_class_types``
    #   "receiver" — from a captured receiver (Go-style)
    #   "none"     — no parent tracking (top-level functions)
    parent_extraction: str = "nesting"

    # Node types that indicate a type context (used with "nesting" mode).
    parent_class_types: frozenset[str] = field(default_factory=frozenset)


# ── Java ──────────────────────────────────────────────────────────────────
_JAVA = LanguageConfig(
    symbol_node_types={
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
        "record_declaration": "record",          # Java 16+ records (kept distinct from class)
        "method_declaration": "method",
        "constructor_declaration": "method",
    },
    body_node_type="block",
    parent_extraction="nesting",
    parent_class_types=frozenset(
        {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
    ),
)


LANGUAGE_CONFIGS: dict[str, LanguageConfig] = {
    "java": _JAVA,
}


def config_for(tag: str) -> LanguageConfig | None:
    """Return the :class:`LanguageConfig` for a language tag, or ``None``."""
    return LANGUAGE_CONFIGS.get(tag)
