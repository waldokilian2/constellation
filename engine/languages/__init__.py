"""
Language support layer for Constellation.

Mirrors the proven data-driven design used by tools like repowise: a language
is **data**, not code. Three artefacts describe a language:

* :class:`~engine.languages.spec.LanguageSpec` — pure identity data
  (extensions, grammar package, query file, ecosystem conventions).
* :class:`~engine.languages.language_config.LanguageConfig` — parser-shape data
  (tree-sitter node-type → canonical symbol kind, parent-extraction mode).
* a tree-sitter ``.scm`` query file in :mod:`engine.queries` using a fixed set
  of capture names (``@sym.def``, ``@sym.name``, ``@call.target``, …).

The :class:`~engine.languages.registry.LanguageRegistry` resolves a file to its
language tag, and :class:`~engine.ast_parser.ASTParser` parses + extracts
generically off the registry — with **zero** ``if lang ==`` branching. Adding a
language is "drop a spec + a ``.scm`` + a config entry"; the parser core never
changes.
"""
from .spec import LanguageSpec
from .language_config import LanguageConfig, LANGUAGE_CONFIGS, config_for
from .registry import LanguageRegistry, REGISTRY, language_for_file

__all__ = [
    "LanguageSpec",
    "LanguageConfig",
    "LANGUAGE_CONFIGS",
    "config_for",
    "LanguageRegistry",
    "REGISTRY",
    "language_for_file",
]
