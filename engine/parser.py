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
    def last_seg(scoped_or_text) -> str:
        """Return the last segment of a dotted name (FQN → simple name)."""
        t = scoped_or_text.text.decode() if hasattr(scoped_or_text, "text") else str(scoped_or_text)
        return t.rsplit(".", 1)[-1] if "." in t else t

    @staticmethod
    def get_annotation_name(annotation_node: Node) -> str:
        """Extract the annotation name (without @), supporting FQN forms.

        @KafkaListener           → "KafkaListener"
        @org.springframework...KafkaListener → "KafkaListener"
        """
        for child in annotation_node.children:
            if child.type == "identifier":
                return child.text.decode()
            if child.type == "scoped_identifier":
                return JavaParser.last_seg(child)
        return ""

    @staticmethod
    def get_annotation_args(annotation_node: Node) -> dict:
        """
        Extract annotation arguments as ``{key: [values]}``.

        A value is always a list because some args are arrays
        (``topics = {"a", "b"}``) and callers may create one entry point per
        element.

        @RabbitListener(queues = "order-events")
          -> {"queues": ["order-events"], "_raw": ["order-events"]}
        @KafkaListener(topics = {"a", "b"})
          -> {"topics": ["a", "b"], "_raw": ["a", "b"]}
        @GetMapping("/api/users")      -> {"_raw": ["/api/users"]}
        @Scheduled(fixedRate = 5000)   -> {"fixedRate": ["5000"]}
        """
        result: dict[str, list[str]] = {}

        def add(key: str, val: str):
            if key:
                result.setdefault(key, []).append(val)

        def is_array(node: Node) -> bool:
            return "array_initializer" in node.type

        def collect_values(node: Node) -> list[str]:
            vals = []
            for c in node.children:
                if c.type == "string_literal":
                    vals.append(JavaParser._extract_string_value(c))
                elif c.type in ("decimal_integer_literal", "decimal_floating_point_literal"):
                    vals.append(c.text.decode())
                elif is_array(c):
                    vals.extend(collect_values(c))
                elif c.type == "identifier":
                    # enum constant / field reference (e.g. RequestMethod.POST)
                    vals.append(c.text.decode())
                elif c.type in ("true", "false"):
                    vals.append(c.text.decode())
            return vals

        for child in annotation_node.children:
            if child.type != "annotation_argument_list":
                continue
            for arg in child.children:
                if arg.type == "element_value_pair":
                    key = ""
                    vals: list[str] = []
                    for pair_child in arg.children:
                        if pair_child.type == "identifier" and not key:
                            key = pair_child.text.decode()
                        elif pair_child.type == "string_literal":
                            vals.append(JavaParser._extract_string_value(pair_child))
                        elif pair_child.type in ("decimal_integer_literal", "decimal_floating_point_literal"):
                            vals.append(pair_child.text.decode())
                        elif is_array(pair_child):
                            vals.extend(collect_values(pair_child))
                        elif pair_child.type == "identifier":
                            vals.append(pair_child.text.decode())
                    for v in vals:
                        add(key, v)
                        add("_raw", v)
                elif arg.type == "string_literal":
                    add("_raw", JavaParser._extract_string_value(arg))
                elif is_array(arg):
                    for v in collect_values(arg):
                        add("_raw", v)
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
                              "void_type", "basic_type",
                              "scoped_identifier", "scoped_type_identifier"):
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
                                           "basic_type", "scoped_identifier",
                                           "scoped_type_identifier"):
                                p["type"] = pc.text.decode()
                            elif pc.type == "identifier":
                                p["name"] = pc.text.decode()
                        if p["name"]:
                            params.append(p)
        return params

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
                                           "basic_type", "scoped_identifier",
                                           "scoped_type_identifier"):
                                p["type"] = pc.text.decode()
                            elif pc.type == "identifier":
                                p["name"] = pc.text.decode()
                        if p["name"]:
                            params.append(p)
        return params

    @staticmethod
    def get_local_variables(method_body: Node) -> dict[str, str]:
        """Map of local variable name → declared type within a method body.

        Handles single- and multi-declarator statements (``Order a = ...``,
        ``Order a = ..., b = ...;``) and generic types (``List<Order>`` → the
        raw simple type is kept). Lets the call graph resolve chained calls on
        locals instead of marking them ``INFERRED``.
        """
        out: dict[str, str] = {}

        def collect(decl: Node):
            type_name = ""
            for c in decl.children:
                if c.type in ("type_identifier", "generic_type", "basic_type",
                              "scoped_identifier", "scoped_type_identifier", "array_type"):
                    type_name = JavaParser.get_type_name(c)
                    break
            for c in decl.children:
                if c.type != "variable_declarator":
                    continue
                name = ""
                for vc in c.children:
                    if vc.type == "identifier" and not name:
                        name = vc.text.decode()
                if name and type_name:
                    out[name] = type_name

        stack = [method_body]
        while stack:
            node = stack.pop()
            if node.type == "local_variable_declaration":
                collect(node)
            for child in node.children:
                stack.append(child)
        return out

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

        from("kafka:orders").to("kafka:ship")   # chained — receiver is itself a call
          -> the inner from() -> {"receiver": "", "method": "from", ...}
          -> the outer to()   -> {"receiver": "", "method": "to", ...}
        """
        result = {"receiver": "", "method": "", "args": []}
        children = list(invocation_node.children)
        has_dot = any(c.type == "." for c in children)

        # Method name = the identifier immediately preceding the argument_list.
        arglist_idx = next(
            (i for i, c in enumerate(children) if c.type == "argument_list"), -1
        )
        if arglist_idx > 0 and children[arglist_idx - 1].type == "identifier":
            result["method"] = children[arglist_idx - 1].text.decode()

        # Receiver = the object expression (first child) only when it is a
        # simple/field name followed by ".". Chained receivers (themselves
        # method_invocations) are left empty so callers don't treat the method
        # name as a receiver.
        if (children and has_dot
                and children[0].type in ("identifier", "field_access")
                and len(children) > 1 and children[1].type == "."):
            result["receiver"] = children[0].text.decode()

        # Arguments.
        if arglist_idx >= 0:
            for arg in children[arglist_idx].children:
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

    # ── Structural helpers (type-aware detection) ──────────────────

    @staticmethod
    def get_package(root: Node) -> str:
        """Package name as a dotted string (empty if default package)."""
        for child in root.children:
            if child.type == "package_declaration":
                for cc in child.children:
                    if cc.type in ("scoped_identifier", "identifier"):
                        return cc.text.decode()
        return ""

    @staticmethod
    def get_imports(root: Node) -> tuple[list[str], list[str]]:
        """Return ``(explicit_fqns, wildcard_packages)``.

        ``explicit_fqns`` are fully-qualified imported types; ``wildcard_packages``
        are packages imported via ``import x.y.*`` (without the trailing ``.*``).
        """
        explicit: list[str] = []
        wildcard: list[str] = []
        for child in root.children:
            if child.type != "import_declaration":
                continue
            text = child.text.decode()
            fqn = ""
            for cc in child.children:
                if cc.type in ("scoped_identifier", "identifier") and not fqn:
                    fqn = cc.text.decode()
            if "*" in text and fqn:
                wildcard.append(fqn)
            elif fqn:
                explicit.append(fqn)
        return explicit, wildcard

    @staticmethod
    def find_types(root: Node) -> list[tuple[Node, str]]:
        """Find all type declarations: (node, kind) where kind is class/interface/enum/record."""
        results: list[tuple[Node, str]] = []

        def walk(node: Node):
            if node.type in ("class_declaration", "interface_declaration",
                             "enum_declaration", "record_declaration"):
                results.append((node, node.type.replace("_declaration", "")))
            for child in node.children:
                walk(child)

        walk(root)
        return results

    @staticmethod
    def get_type_name(node: Optional[Node]) -> str:
        """Reduce a type node to its simple/raw type name.

        ``KafkaTemplate<String, Object>`` → ``KafkaTemplate``;
        ``com.acme.Foo`` → ``Foo``; ``String[]`` → ``String``.
        """
        if node is None:
            return ""
        t = node.type
        if t == "generic_type":
            for c in node.children:
                if c.type in ("type_identifier", "scoped_identifier", "scoped_type_identifier"):
                    return JavaParser.get_type_name(c)
            return ""
        if t in ("scoped_identifier", "scoped_type_identifier"):
            return JavaParser.last_seg(node)
        if t in ("type_identifier", "basic_type", "void_type"):
            return node.text.decode()
        if t == "array_type":
            for c in node.children:
                if c.type in ("type_identifier", "generic_type", "scoped_identifier", "scoped_type_identifier", "basic_type"):
                    return JavaParser.get_type_name(c)
        # Fallback: first type-ish descendant.
        for c in node.children:
            if c.type in ("type_identifier", "generic_type", "scoped_identifier", "scoped_type_identifier", "basic_type"):
                return JavaParser.get_type_name(c)
        return node.text.decode().strip()

    @staticmethod
    def get_supertypes(class_node: Node) -> list[str]:
        """Simple names from ``extends`` and ``implements`` clauses."""
        out: list[str] = []

        def collect(clause: Node):
            for c in clause.children:
                if c.type in ("type_identifier", "generic_type", "scoped_identifier", "scoped_type_identifier"):
                    out.append(JavaParser.get_type_name(c))
                elif c.type in ("type_list", "interface_type_list"):
                    collect(c)

        for child in class_node.children:
            if child.type in ("superclass", "super_interfaces"):
                collect(child)
        seen: set[str] = set()
        uniq: list[str] = []
        for s in out:
            if s and s not in seen:
                seen.add(s)
                uniq.append(s)
        return uniq

    @staticmethod
    def get_fields(class_node: Node) -> list[dict]:
        """All field declarations in a class.

        Returns ``[{name, type, is_static_final, const_value}]`` where
        ``const_value`` is the string literal initializer of a
        ``static final String`` constant (else ``None``).
        """
        fields: list[dict] = []

        def walk(node: Node):
            if node.type == "field_declaration":
                fields.extend(JavaParser._parse_field(node))
            for child in node.children:
                walk(child)

        walk(class_node)
        return fields

    @staticmethod
    def _parse_field(field_node: Node) -> list[dict]:
        type_name = ""
        is_static_final = False
        for c in field_node.children:
            if c.type == "modifiers":
                mods = c.text.decode().split()
                if "static" in mods and "final" in mods:
                    is_static_final = True
            elif c.type in ("type_identifier", "generic_type", "basic_type", "scoped_identifier", "scoped_type_identifier"):
                type_name = JavaParser.get_type_name(c)
        out: list[dict] = []
        for c in field_node.children:
            if c.type != "variable_declarator":
                continue
            name = ""
            const: Optional[str] = None
            for vc in c.children:
                if vc.type == "identifier" and not name:
                    name = vc.text.decode()
                elif vc.type == "string_literal":
                    const = JavaParser._extract_string_value(vc)
            if name:
                out.append({
                    "name": name,
                    "type": type_name,
                    "is_static_final": is_static_final,
                    "const_value": const,
                })
        return out

    @staticmethod
    def get_method_signature(method_node: Node) -> dict:
        """``{name, param_types: [...], return_type}`` for overload resolution."""
        name = ""
        ret = ""
        params: list[str] = []
        for c in method_node.children:
            if c.type == "identifier" and not name:
                name = c.text.decode()
            elif c.type in ("type_identifier", "generic_type", "basic_type", "void_type", "scoped_identifier", "scoped_type_identifier"):
                if not ret:
                    ret = JavaParser.get_type_name(c)
            elif c.type == "formal_parameters":
                for pc in c.children:
                    if pc.type != "formal_parameter":
                        continue
                    ptype = ""
                    for fpc in pc.children:
                        if fpc.type in ("type_identifier", "generic_type", "basic_type", "scoped_identifier", "scoped_type_identifier"):
                            if not ptype:
                                ptype = JavaParser.get_type_name(fpc)
                    if ptype:
                        params.append(ptype)
        return {"name": name, "param_types": params, "return_type": ret}

    @staticmethod
    def find_nested_annotations(annotation_node: Node) -> list[Node]:
        """All annotation nodes nested inside an annotation (e.g. each
        ``@ActivationConfigProperty`` inside ``@MessageDriven(activationConfig={...})``).

        Does not include the annotation itself.
        """
        out: list[Node] = []

        def walk(n: Node):
            for c in n.children:
                if c.type in ("annotation", "marker_annotation"):
                    out.append(c)
                else:
                    walk(c)

        walk(annotation_node)
        return out

    @staticmethod
    def get_method_params_annotated(method_node: Node) -> list[dict]:
        """Method params with their annotation names: ``[{name, type, annotations}]``.

        Used for CDI ``@Observes`` (a parameter annotation) and ``@PathParam``.
        """
        out: list[dict] = []
        for c in method_node.children:
            if c.type != "formal_parameters":
                continue
            for pc in c.children:
                if pc.type != "formal_parameter":
                    continue
                name = ""
                ptype = ""
                anns: list[str] = []
                for fpc in pc.children:
                    if fpc.type == "modifiers":
                        for mc in fpc.children:
                            if mc.type in ("annotation", "marker_annotation"):
                                anns.append(JavaParser.get_annotation_name(mc))
                    elif fpc.type in ("type_identifier", "generic_type", "basic_type", "scoped_identifier", "scoped_type_identifier"):
                        if not ptype:
                            ptype = JavaParser.get_type_name(fpc)
                    elif fpc.type == "identifier" and not name:
                        name = fpc.text.decode()
                if name or ptype:
                    out.append({"name": name, "type": ptype, "annotations": anns})
        return out
