"""
:class:`LanguageRegistry` — resolves files to languages.

The single source of truth for "what language is this file?". Built once from
``ALL_SPECS``; consumers (the orchestrator's file discovery, the parser)
derive everything from it. Adding a language never touches this module — it
only registers a new spec in :mod:`engine.languages.specs`.
"""
from __future__ import annotations
from pathlib import Path

from .spec import LanguageSpec
from .specs import ALL_SPECS


class LanguageRegistry:
    """Extension/filename → :class:`LanguageSpec` lookup."""

    def __init__(self, specs: tuple[LanguageSpec, ...] = ALL_SPECS):
        self._specs: tuple[LanguageSpec, ...] = specs
        # first-spec-wins extension map
        self._by_ext: dict[str, LanguageSpec] = {}
        self._by_tag: dict[str, LanguageSpec] = {}
        self._by_special: dict[str, LanguageSpec] = {}
        for spec in specs:
            self._by_tag[spec.tag] = spec
            for ext in spec.extensions:
                self._by_ext.setdefault(ext, spec)
            for name in spec.special_filenames:
                self._by_special.setdefault(name, spec)

    # ── lookup ──────────────────────────────────────────────────────

    def all_specs(self) -> tuple[LanguageSpec, ...]:
        return self._specs

    def get(self, tag: str) -> LanguageSpec | None:
        return self._by_tag.get(tag)

    def for_file(self, path: str | Path) -> LanguageSpec | None:
        """Resolve a file path to its language spec, or ``None`` if unknown."""
        p = Path(path)
        name = p.name
        spec = self._by_special.get(name)
        if spec:
            return spec
        suffix = "".join(p.suffixes).lower()
        if suffix in self._by_ext:
            return self._by_ext[suffix]
        # fall back to the last suffix (handles compound like .gradle.kts → .kts misses)
        return self._by_ext.get(p.suffix.lower())

    def extensions(self) -> frozenset[str]:
        """All registered source extensions."""
        return frozenset(self._by_ext.keys())

    def test_filter(self, path: str | Path, spec: LanguageSpec) -> bool:
        """True if *path* looks like a test file for *spec*'s language.

        Uses the spec's test-dir paths/tokens and camel-case suffixes — these
        are per-language conventions that must not leak across languages
        (``latest.java`` is not a test; ``OrderTest.java`` is).
        """
        s = str(path).replace("\\", "/").lower()
        for seg in spec.test_dir_paths:
            if seg.replace("\\", "/") in s:
                return True
        parts = [pp for pp in s.split("/") if pp]
        if any(tok in spec.test_dir_tokens for tok in parts):
            return True
        stem = Path(path).stem
        return any(stem.endswith(suf) for suf in spec.test_camel_suffixes)


# Process-wide singleton.
REGISTRY = LanguageRegistry()


def language_for_file(path: str | Path) -> LanguageSpec | None:
    """Convenience: spec for a file path via the global :data:`REGISTRY`."""
    return REGISTRY.for_file(path)
