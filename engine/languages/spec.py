"""
:class:`LanguageSpec` — pure identity data for a single language.

Deliberately holds *identity* only (file matching, grammar wiring, ecosystem
conventions). Parser-shape concerns (node-type → kind, parent extraction) live
in :mod:`engine.languages.language_config`. This keeps the registry a leaf
dependency that imports nothing from the parsing pipeline.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LanguageSpec:
    """Complete identity specification for a single language."""

    # -- Identity --------------------------------------------------------
    tag: str                         # matches the canonical language id ("java")
    display_name: str                # "Java", "Python"

    # -- File matching ---------------------------------------------------
    extensions: frozenset[str] = field(default_factory=frozenset)   # {".java"}
    special_filenames: frozenset[str] = field(default_factory=frozenset)  # {"Dockerfile"}

    # -- Tree-sitter -----------------------------------------------------
    grammar_package: str | None = None      # import name, e.g. "tree_sitter_java"
    grammar_loader: str = "language"        # attr on the grammar package returning the language ptr
    scm_file: str | None = None             # "java.scm" — None = no AST queries
    shares_grammar_with: str | None = None  # a language reuses another's grammar (C → cpp)

    # -- Heritage --------------------------------------------------------
    # Node types that declare a type (class/interface/…), used by heritage walks.
    heritage_node_types: frozenset[str] = field(default_factory=frozenset)

    # -- Resolution quality (tiering) -----------------------------------
    #   "full"    — dedicated resolver, validated mechanics
    #   "partial" — dedicated resolver with known gaps
    #   "none"    — generic stem-lookup fallback only
    import_support: str = "none"

    # -- Entry / test conventions (detection + filtering) ---------------
    # Filename stems that mark an entry point for this language.
    entry_stems: tuple[str, ...] = ()
    # Test-path conventions consumed by the orchestrator's test filter.
    test_dir_paths: tuple[str, ...] = ()        # multi-segment ("src/test/java")
    test_dir_tokens: tuple[str, ...] = ()       # single-segment dir tokens ("test", "tests")
    test_camel_suffixes: tuple[str, ...] = ()   # ("Test", "Tests", "IT")

    # -- Ecosystem -------------------------------------------------------
    manifest_files: tuple[str, ...] = ()        # ("pom.xml", "build.gradle")
    blocked_dirs: tuple[str, ...] = ()
    blocked_extensions: tuple[str, ...] = ()

    # -- Builtins (filtered from call graphs) ---------------------------
    builtin_calls: frozenset[str] = field(default_factory=frozenset)

    # -- Display ---------------------------------------------------------
    color_hex: str = "#8b5cf6"
