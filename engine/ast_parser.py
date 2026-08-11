"""
:class:`ASTParser` — the language-agnostic parsing + extraction core.

Replaces the old Java-only ``JavaParser``. The parser holds **no** language-
specific logic: every branch is driven by :mod:`engine.languages` data (which
grammar to load, which ``.scm`` query to compile, which node type is which
kind). Adding a language is a data change, not a code change here.

Design (verified against tree-sitter 0.26):
  * grammars are imported lazily from ``LanguageSpec.grammar_package`` and
    cached process-wide;
  * ``.scm`` queries are compiled once per language and cached;
  * :meth:`extract` runs the query and groups captures by ``@sym.def`` node so
    a definition matching several patterns (with/without modifiers) collapses
    into one :class:`SymbolRecord` with merged modifiers.
"""
from __future__ import annotations
from importlib import import_module
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from tree_sitter import Language, Node, Parser, Query, QueryCursor

from .languages import LanguageRegistry, REGISTRY, config_for, LanguageSpec

_QUERIES_DIR = Path(__file__).resolve().parent / "queries"


# ── extraction records (language-neutral) ─────────────────────────────────


@dataclass
class SymbolRecord:
    """A discovered symbol (class / method / function / …)."""
    kind: str                              # canonical kind from LanguageConfig
    name: str
    def_node: Node                         # the full definition node
    params_node: Optional[Node] = None     # parameter list (methods)
    modifier_nodes: list[Node] = field(default_factory=list)  # annotations/decorators


@dataclass
class ImportRecord:
    """A single import statement."""
    statement_node: Node
    module: str


@dataclass
class CallRecord:
    """A call site discovered structurally."""
    site_node: Node
    target: str
    receiver: str
    arguments_node: Optional[Node] = None


@dataclass
class Extraction:
    """Result of running a language's query over a root node."""
    symbols: list[SymbolRecord] = field(default_factory=list)
    imports: list[ImportRecord] = field(default_factory=list)
    calls: list[CallRecord] = field(default_factory=list)


# ── parser ────────────────────────────────────────────────────────────────


def _node_text(node: Node) -> str:
    try:
        return node.text.decode()
    except Exception:
        return ""


class ASTParser:
    """Grammar + query engine for all registered languages."""

    def __init__(self, registry: LanguageRegistry = REGISTRY):
        self._registry = registry
        self._languages: dict[str, Language] = {}
        self._parsers: dict[str, Parser] = {}
        self._queries: dict[str, Optional[Query]] = {}

    # ── grammar / query wiring ──────────────────────────────────────

    def _language_for(self, tag: str) -> Optional[Language]:
        """Lazily import + cache a tree-sitter Language for *tag*."""
        if tag in self._languages:
            return self._languages[tag]
        spec = self._registry.get(tag)
        if spec is None:
            return None
        # A language may share another's grammar (e.g. C → cpp).
        if spec.shares_grammar_with:
            shared = self._language_for(spec.shares_grammar_with)
            self._languages[tag] = shared
            return shared
        if not spec.grammar_package:
            return None
        try:
            mod = import_module(spec.grammar_package)
            loader = getattr(mod, spec.grammar_loader)
            lang = Language(loader())
            self._languages[tag] = lang
            return lang
        except Exception:
            return None

    def _parser_for(self, tag: str) -> Optional[Parser]:
        lang = self._language_for(tag)
        if lang is None:
            return None
        if tag not in self._parsers:
            self._parsers[tag] = Parser(lang)
        return self._parsers[tag]

    def _query_for(self, tag: str) -> Optional[Query]:
        if tag in self._queries:
            return self._queries[tag]
        spec = self._registry.get(tag)
        scm_name = (spec.scm_file if spec and spec.scm_file else None) or f"{tag}.scm"
        scm_path = _QUERIES_DIR / scm_name
        lang = self._language_for(tag)
        result: Optional[Query] = None
        if lang is not None and scm_path.exists():
            try:
                result = Query(lang, scm_path.read_text(encoding="utf-8"))
            except Exception:
                result = None
        self._queries[tag] = result
        return result

    # ── parsing ─────────────────────────────────────────────────────

    def parse_source(self, source: bytes, tag: str = "java") -> Optional[Node]:
        """Parse raw source bytes for *tag*; return the root AST node."""
        parser = self._parser_for(tag)
        if parser is None:
            return None
        return parser.parse(source).root_node

    def parse_file(self, file_path: Path, tag: str = "java") -> Optional[Node]:
        """Parse a file for *tag*; return the root node or ``None`` on error."""
        try:
            source = file_path.read_bytes()
        except OSError:
            return None
        return self.parse_source(source, tag)

    # ── generic extraction ──────────────────────────────────────────

    def extract(self, tag: str, root: Node) -> Extraction:
        """Run *tag*'s ``.scm`` query over *root* and return typed records.

        Symbols matching several patterns (e.g. a class with and without a
        ``modifiers`` child) collapse into one :class:`SymbolRecord`, with
        modifier captures merged — so callers always see one record per
        definition.
        """
        result = Extraction()
        query = self._query_for(tag)
        if query is None:
            return result
        cfg = config_for(tag)
        kind_map = cfg.symbol_node_types if cfg else {}

        cursor = QueryCursor(query)
        symbols_by_node: dict[int, SymbolRecord] = {}

        for _pattern_index, caps in cursor.matches(root):
            # ── symbols ──
            if "sym.def" in caps:
                for def_node in caps["sym.def"]:
                    kind = kind_map.get(def_node.type, def_node.type)
                    rec = symbols_by_node.get(id(def_node))
                    if rec is None:
                        rec = SymbolRecord(kind=kind, name="", def_node=def_node)
                        symbols_by_node[id(def_node)] = rec
                    names = caps.get("sym.name", [])
                    if names and not rec.name:
                        rec.name = _node_text(names[0])
                    if "sym.params" in caps and rec.params_node is None:
                        rec.params_node = caps["sym.params"][0]
                    rec.modifier_nodes.extend(caps.get("sym.modifiers", []))
                continue

            # ── imports ──
            if "import.statement" in caps:
                stmt = caps["import.statement"][0]
                mods = caps.get("import.module", [])
                module = _node_text(mods[0]) if mods else ""
                result.imports.append(ImportRecord(statement_node=stmt, module=module))
                continue

            # ── calls ──
            if "call.site" in caps:
                site = caps["call.site"][0]
                target = _node_text(caps["call.target"][0]) if "call.target" in caps else ""
                receiver = _node_text(caps["call.receiver"][0]) if "call.receiver" in caps else ""
                args = caps["call.arguments"][0] if "call.arguments" in caps else None
                result.calls.append(
                    CallRecord(site_node=site, target=target, receiver=receiver, arguments_node=args)
                )

        result.symbols = list(symbols_by_node.values())
        return result

    # ── convenience: parse + extract in one call ────────────────────

    def extract_file(self, file_path: Path, tag: str = "java") -> Extraction:
        root = self.parse_file(file_path, tag)
        if root is None:
            return Extraction()
        return self.extract(tag, root)
