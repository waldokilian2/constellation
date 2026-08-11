"""
Tests for the language data layer + ASTParser (Phase 1 infrastructure).

Stdlib-only — repo convention (no pytest).
"""
from __future__ import annotations
from pathlib import Path

from engine.languages import REGISTRY, language_for_file, LANGUAGE_CONFIGS, config_for
from engine.ast_parser import ASTParser


def test_registry_resolves_java():
    spec = language_for_file("src/main/Order.java")
    assert spec is not None and spec.tag == "java"
    assert ".java" in REGISTRY.extensions()


def test_registry_unknown_extension_is_none():
    assert language_for_file("README.md") is None
    assert language_for_file("notes.txt") is None


def test_language_config_has_java_kinds():
    cfg = config_for("java")
    assert cfg is not None
    assert cfg.symbol_node_types["class_declaration"] == "class"
    assert cfg.symbol_node_types["method_declaration"] == "method"
    assert cfg.symbol_node_types["record_declaration"] == "record"


def test_astparser_extracts_java_symbols_imports_calls():
    p = ASTParser()
    src = b"""
    package com.acme;
    import java.util.List;
    import org.springframework.kafka.annotation.KafkaListener;

    class OrderService {
        @KafkaListener(topics = "orders")
        void onOrder(String msg) {
            svc.process(msg);
        }
    }
    """
    root = p.parse_source(src, "java")
    assert root is not None
    ext = p.extract("java", root)

    kinds = {(s.kind, s.name) for s in ext.symbols}
    assert ("class", "OrderService") in kinds
    assert ("method", "onOrder") in kinds

    # Each definition appears once even though several patterns could match.
    cls = [s for s in ext.symbols if s.name == "OrderService"]
    assert len(cls) == 1

    imports = {i.module for i in ext.imports}
    assert "java.util.List" in imports
    assert "org.springframework.kafka.annotation.KafkaListener" in imports

    targets = {c.target for c in ext.calls}
    assert "process" in targets


def test_astparser_extracts_interface_enum_record():
    p = ASTParser()
    src = b"""
    interface Repo {}
    enum Color { RED, GREEN }
    record Point(int x, int y) {}
    """
    ext = p.extract("java", p.parse_source(src, "java"))
    kinds = {(s.kind, s.name) for s in ext.symbols}
    assert ("interface", "Repo") in kinds
    assert ("enum", "Color") in kinds
    assert ("record", "Point") in kinds


def test_registry_test_filter_is_per_language():
    """OrderTest.java is a test; latest.java is not (per-language convention)."""
    spec = REGISTRY.get("java")
    assert REGISTRY.test_filter("src/test/java/com/x/OrderTest.java", spec) is True
    assert REGISTRY.test_filter("src/main/java/com/x/latest.java", spec) is False
