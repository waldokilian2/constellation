"""
Tree-sitter AST parser wrapper for Java.
Handles file reading, parsing, and provides query helpers.
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
import tree_sitter_java
from tree_sitter import Parser, Language, Node


class JavaParser:
    """Wraps tree-sitter for Java parsing."""

    def __init__(self):
        self.language = Language(tree_sitter_java.language())
        self.parser = Parser(self.language)

    def parse_file(self, file_path: Path) -> Optional[Node]:
        """Parse a Java file and return the root AST node."""
        try:
            source = file_path.read_bytes()
            tree = self.parser.parse(source)
            return tree.root_node
        except Exception as e:
            return None

    def parse_source(self, source: bytes) -> Node:
        """Parse raw Java source bytes and return the root AST node."""
        tree = self.parser.parse(source)
        return tree.root_node

    # ── AST query helpers ──────────────────────────────────────────

    @staticmethod
    def find_classes(root: Node) -> list[Node]:
        """Find all class_declaration nodes in the AST."""
        results = []

        def walk(node: Node):
            if node.type == "class_declaration":
                results.append(node)
            for child in node.children:
                walk(child)

        walk(root)
        return results

    @staticmethod
    def get_class_name(class_node: Node) -> str:
        """Extract the class name from a class_declaration node."""
        for child in class_node.children:
            if child.type == "identifier":
                return child.text.decode()
        return ""

    @staticmethod
    def get_class_annotations(class_node: Node) -> list[Node]:
        """Get all annotations on a class."""
        for child in class_node.children:
            if child.type == "modifiers":
                return [
                    c for c in child.children
                    if c.type in ("annotation", "marker_annotation")
                ]
        return []

    @staticmethod
    def find_methods(class_node: Node) -> list[Node]:
        """Find all method_declaration nodes within a class."""
        results = []

        def walk(node: Node):
            if node.type == "method_declaration":
                results.append(node)
            for child in node.children:
                walk(child)

        walk(class_node)
        return results

    @staticmethod
    def get_method_name(method_node: Node) -> str:
        """Extract the method name from a method_declaration node."""
        for child in method_node.children:
            if child.type == "identifier":
                return child.text.decode()
        return ""

    @staticmethod
    def get_method_annotations(method_node: Node) -> list[Node]:
        """Get all annotations on a method."""
        for child in method_node.children:
            if child.type == "modifiers":
                return [
                    c for c in child.children
                    if c.type in ("annotation", "marker_annotation")
                ]
        return []

    @staticmethod
    def get_annotation_name(annotation_node: Node) -> str:
        """Extract the annotation name (without @)."""
        # For both annotation and marker_annotation, the name is
        # the first identifier child after @
        for child in annotation_node.children:
            if child.type == "identifier":
                return child.text.decode()
        return ""

    @staticmethod
    def get_annotation_args(annotation_node: Node) -> dict:
        """
        Extract annotation arguments as a dict.
        Handles both key=value pairs and bare string values.

        @RabbitListener(queues = "order-events")
          -> {"queues": "order-events", "_raw": "order-events"}

        @KafkaListener(topics = "payment-events")
          -> {"topics": "payment-events", "_raw": "payment-events"}

        @GetMapping("/api/users")
          -> {"_raw": "/api/users"}
        """
        result = {}
        for child in annotation_node.children:
            if child.type == "annotation_argument_list":
                for arg in child.children:
                    if arg.type == "element_value_pair":
                        # key = value form
                        key = ""
                        value = ""
                        for pair_child in arg.children:
                            if pair_child.type == "identifier":
                                key = pair_child.text.decode()
                            elif pair_child.type == "string_literal":
                                value = JavaParser._extract_string_value(pair_child)
                        if key:
                            result[key] = value
                    elif arg.type == "string_literal":
                        # bare string value
                        result["_raw"] = JavaParser._extract_string_value(arg)
        return result

    @staticmethod
    def _extract_string_value(string_node: Node) -> str:
        """Extract the value from a string_literal node (without quotes)."""
        for child in string_node.children:
            if child.type == "string_fragment":
                return child.text.decode()
        # Fallback: strip quotes from the text
        text = string_node.text.decode()
        if len(text) >= 2 and text[0] in ('"', "'"):
            return text[1:-1]
        return text

    @staticmethod
    def get_method_return_type(method_node: Node) -> str:
        """Extract the return type of a method."""
        for child in method_node.children:
            if child.type in ("type_identifier", "generic_type",
                              "void_type", "basic_type"):
                return child.text.decode()
        return ""

    @staticmethod
    def get_method_parameters(method_node: Node) -> list[dict]:
        """
        Extract method parameters.
        Returns list of {name, type}.
        """
        params = []
        for child in method_node.children:
            if child.type == "formal_parameters":
                for param in child.children:
                    if param.type == "formal_parameter":
                        p = {"name": "", "type": ""}
                        for pc in param.children:
                            if pc.type in ("type_identifier", "generic_type",
                                           "basic_type"):
                                p["type"] = pc.text.decode()
                            elif pc.type == "identifier":
                                p["name"] = pc.text.decode()
                        if p["name"]:
                            params.append(p)
        return params

    @staticmethod
    def find_method_invocations(method_body: Node) -> list[Node]:
        """Find all method_invocation nodes within a method body."""
        results = []

        def walk(node: Node):
            if node.type == "method_invocation":
                results.append(node)
            for child in node.children:
                walk(child)

        walk(method_body)
        return results

    @staticmethod
    def parse_method_invocation(invocation_node: Node) -> dict:
        """
        Parse a method_invocation node into its components.

        orderService.process(msg)
          -> {"receiver": "orderService", "method": "process", "args": ["msg"]}

        someMethod()
          -> {"receiver": "", "method": "someMethod", "args": []}

        rabbitTemplate.convertAndSend("order-events", msg)
          -> {"receiver": "rabbitTemplate", "method": "convertAndSend",
              "args": ["order-events", "msg"]}
        """
        result = {"receiver": "", "method": "", "args": []}

        for child in invocation_node.children:
            if child.type == "identifier":
                # Could be receiver or method name — depends on position
                if not result["receiver"] and any(
                    sib.type == "." for sib in invocation_node.children
                ):
                    result["receiver"] = child.text.decode()
                else:
                    result["method"] = child.text.decode()
            elif child.type == "field_access":
                # obj.field.method() — extract receiver
                result["receiver"] = child.text.decode()
            elif child.type == "argument_list":
                # Extract arguments
                for arg in child.children:
                    if arg.type == "string_literal":
                        result["args"].append(
                            JavaParser._extract_string_value(arg)
                        )
                    elif arg.type == "identifier":
                        result["args"].append(arg.text.decode())
                    elif arg.type in ("method_invocation", "field_access"):
                        result["args"].append(arg.text.decode())
                    elif arg.type == "decimal_integer_literal":
                        result["args"].append(arg.text.decode())

        return result

    @staticmethod
    def get_method_body(method_node: Node) -> Optional[Node]:
        """Get the block (body) of a method, or None if abstract/interface."""
        for child in method_node.children:
            if child.type == "block":
                return child
        return None

    @staticmethod
    def get_enclosing_class_name(node: Node) -> str:
        """Walk up the AST to find the enclosing class name."""
        current = node.parent
        while current:
            if current.type == "class_declaration":
                return JavaParser.get_class_name(current)
            current = current.parent
        return ""
