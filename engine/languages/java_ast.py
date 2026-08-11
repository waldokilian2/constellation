"""
Java AST interpretation — the Java language backend.

These are pure functions over tree-sitter-java nodes: they interpret Java
*semantics* that a declarative ``.scm`` capture cannot express (annotation
argument shapes, recursive type reduction, supertype clauses, method
signatures, call-site parsing). Grammar wiring + structural *discovery* live in
:mod:`engine.ast_parser`; this module is the Java-specific interpretation layer
that the symbol index, detector and call-graph builder call into.

Each target language gets an analogous backend module; the parser core never
branches on language.
"""
from __future__ import annotations
from typing import Optional
from tree_sitter import Node


# ── name / type reduction ─────────────────────────────────────────────────


def last_seg(scoped_or_text) -> str:
    """Return the last segment of a dotted name (FQN → simple name)."""
    t = scoped_or_text.text.decode() if hasattr(scoped_or_text, "text") else str(scoped_or_text)
    return t.rsplit(".", 1)[-1] if "." in t else t


def get_class_name(class_node: Node) -> str:
    """Extract the name from a class/interface/enum/record declaration."""
    for child in class_node.children:
        if child.type == "identifier":
            return child.text.decode()
    return ""


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
                return get_type_name(c)
        return ""
    if t in ("scoped_identifier", "scoped_type_identifier"):
        return last_seg(node)
    if t in ("type_identifier", "basic_type", "void_type"):
        return node.text.decode()
    if t == "array_type":
        for c in node.children:
            if c.type in ("type_identifier", "generic_type", "scoped_identifier", "scoped_type_identifier", "basic_type"):
                return get_type_name(c)
    # Fallback: first type-ish descendant.
    for c in node.children:
        if c.type in ("type_identifier", "generic_type", "scoped_identifier", "scoped_type_identifier", "basic_type"):
            return get_type_name(c)
    return node.text.decode().strip()


# ── structural discovery (within a node) ──────────────────────────────────


def find_methods(class_node: Node) -> list[Node]:
    """All method_declaration nodes within a type (recursive — includes nested)."""
    results: list[Node] = []

    def walk(node: Node):
        if node.type == "method_declaration":
            results.append(node)
        for child in node.children:
            walk(child)

    walk(class_node)
    return results


def get_method_name(method_node: Node) -> str:
    for child in method_node.children:
        if child.type == "identifier":
            return child.text.decode()
    return ""


def get_enclosing_class_name(node: Node) -> str:
    """Walk up the AST to find the enclosing type's name."""
    current = node.parent
    while current:
        if current.type in ("class_declaration", "interface_declaration",
                            "enum_declaration", "record_declaration"):
            return get_class_name(current)
        current = current.parent
    return ""


# ── annotations ───────────────────────────────────────────────────────────


def _annotations_from_modifiers(holder: Node) -> list[Node]:
    for child in holder.children:
        if child.type == "modifiers":
            return [c for c in child.children if c.type in ("annotation", "marker_annotation")]
    return []


def get_class_annotations(class_node: Node) -> list[Node]:
    return _annotations_from_modifiers(class_node)


def get_method_annotations(method_node: Node) -> list[Node]:
    return _annotations_from_modifiers(method_node)


def get_annotation_name(annotation_node: Node) -> str:
    """Annotation name without ``@``, supporting FQN forms.

    @KafkaListener                          → "KafkaListener"
    @org.springframework...KafkaListener    → "KafkaListener"
    """
    for child in annotation_node.children:
        if child.type == "identifier":
            return child.text.decode()
        if child.type == "scoped_identifier":
            return last_seg(child)
    return ""


def extract_string_value(string_node: Node) -> str:
    """Extract the value from a string_literal node (without quotes)."""
    for child in string_node.children:
        if child.type == "string_fragment":
            return child.text.decode()
    text = string_node.text.decode()
    if len(text) >= 2 and text[0] in ('"', "'"):
        return text[1:-1]
    return text


def get_annotation_args(annotation_node: Node) -> dict:
    """Extract annotation arguments as ``{key: [values]}``.

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

    def is_array(n: Node) -> bool:
        return "array_initializer" in n.type

    def collect_values(n: Node) -> list[str]:
        vals = []
        for c in n.children:
            if c.type == "string_literal":
                vals.append(extract_string_value(c))
            elif c.type in ("decimal_integer_literal", "decimal_floating_point_literal"):
                vals.append(c.text.decode())
            elif is_array(c):
                vals.extend(collect_values(c))
            elif c.type == "identifier":
                vals.append(c.text.decode())  # enum constant / field reference
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
                        vals.append(extract_string_value(pair_child))
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
                add("_raw", extract_string_value(arg))
            elif is_array(arg):
                for v in collect_values(arg):
                    add("_raw", v)
    return result


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


# ── method shape ──────────────────────────────────────────────────────────


def get_method_return_type(method_node: Node) -> str:
    for child in method_node.children:
        if child.type in ("type_identifier", "generic_type", "void_type", "basic_type",
                          "scoped_identifier", "scoped_type_identifier"):
            return child.text.decode()
    return ""


def get_method_parameters(method_node: Node) -> list[dict]:
    """Method parameters as ``[{name, type}]``."""
    params = []
    for child in method_node.children:
        if child.type == "formal_parameters":
            for param in child.children:
                if param.type == "formal_parameter":
                    p = {"name": "", "type": ""}
                    for pc in param.children:
                        if pc.type in ("type_identifier", "generic_type", "basic_type",
                                       "scoped_identifier", "scoped_type_identifier"):
                            p["type"] = pc.text.decode()
                        elif pc.type == "identifier":
                            p["name"] = pc.text.decode()
                    if p["name"]:
                        params.append(p)
    return params


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
                            anns.append(get_annotation_name(mc))
                elif fpc.type in ("type_identifier", "generic_type", "basic_type", "scoped_identifier", "scoped_type_identifier"):
                    if not ptype:
                        ptype = get_type_name(fpc)
                elif fpc.type == "identifier" and not name:
                    name = fpc.text.decode()
            if name or ptype:
                out.append({"name": name, "type": ptype, "annotations": anns})
    return out


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
                ret = get_type_name(c)
        elif c.type == "formal_parameters":
            for pc in c.children:
                if pc.type != "formal_parameter":
                    continue
                ptype = ""
                for fpc in pc.children:
                    if fpc.type in ("type_identifier", "generic_type", "basic_type", "scoped_identifier", "scoped_type_identifier"):
                        if not ptype:
                            ptype = get_type_name(fpc)
                if ptype:
                    params.append(ptype)
    return {"name": name, "param_types": params, "return_type": ret}


def get_method_body(method_node: Node) -> Optional[Node]:
    """Get the block (body) of a method, or None if abstract/interface."""
    for child in method_node.children:
        if child.type == "block":
            return child
    return None


def get_local_variables(method_body: Node) -> dict[str, str]:
    """Map of local variable name → declared type within a method body.

    Handles single- and multi-declarator statements and generic types so chained
    calls on locals resolve to ``EXTRACTED`` instead of staying ``INFERRED``.
    """
    out: dict[str, str] = {}

    def collect(decl: Node):
        type_name = ""
        for c in decl.children:
            if c.type in ("type_identifier", "generic_type", "basic_type",
                          "scoped_identifier", "scoped_type_identifier", "array_type"):
                type_name = get_type_name(c)
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


# ── calls ─────────────────────────────────────────────────────────────────


def find_method_invocations(method_body: Node) -> list[Node]:
    """All method_invocation nodes within a method body."""
    results: list[Node] = []

    def walk(node: Node):
        if node.type == "method_invocation":
            results.append(node)
        for child in node.children:
            walk(child)

    walk(method_body)
    return results


def parse_method_invocation(invocation_node: Node) -> dict:
    """Parse a method_invocation node into ``{receiver, method, args}``.

    orderService.process(msg)
      -> {"receiver": "orderService", "method": "process", "args": ["msg"]}
    someMethod()
      -> {"receiver": "", "method": "someMethod", "args": []}
    from("kafka:orders").to("kafka:ship")   # chained — receiver is itself a call
      -> inner from() -> {"receiver": "", "method": "from", ...}
      -> outer to()   -> {"receiver": "", "method": "to", ...}
    """
    result = {"receiver": "", "method": "", "args": []}
    children = list(invocation_node.children)
    has_dot = any(c.type == "." for c in children)

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

    if arglist_idx >= 0:
        for arg in children[arglist_idx].children:
            if arg.type == "string_literal":
                result["args"].append(extract_string_value(arg))
            elif arg.type == "identifier":
                result["args"].append(arg.text.decode())
            elif arg.type in ("method_invocation", "field_access"):
                result["args"].append(arg.text.decode())
            elif arg.type == "decimal_integer_literal":
                result["args"].append(arg.text.decode())

    return result


# ── package / imports / fields / supertypes ───────────────────────────────


def get_package(root: Node) -> str:
    """Package name as a dotted string (empty if default package)."""
    for child in root.children:
        if child.type == "package_declaration":
            for cc in child.children:
                if cc.type in ("scoped_identifier", "identifier"):
                    return cc.text.decode()
    return ""


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


def get_supertypes(class_node: Node) -> list[str]:
    """Simple names from ``extends`` and ``implements`` clauses."""
    out: list[str] = []

    def collect(clause: Node):
        for c in clause.children:
            if c.type in ("type_identifier", "generic_type", "scoped_identifier", "scoped_type_identifier"):
                out.append(get_type_name(c))
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


def get_fields(class_node: Node) -> list[dict]:
    """All field declarations in a class.

    Returns ``[{name, type, is_static_final, const_value}]`` where
    ``const_value`` is the string literal initializer of a ``static final
    String`` constant (else ``None``).
    """
    fields: list[dict] = []

    def walk(node: Node):
        if node.type == "field_declaration":
            fields.extend(_parse_field(node))
        for child in node.children:
            walk(child)

    walk(class_node)
    return fields


def _parse_field(field_node: Node) -> list[dict]:
    type_name = ""
    is_static_final = False
    for c in field_node.children:
        if c.type == "modifiers":
            mods = c.text.decode().split()
            if "static" in mods and "final" in mods:
                is_static_final = True
        elif c.type in ("type_identifier", "generic_type", "basic_type", "scoped_identifier", "scoped_type_identifier"):
            type_name = get_type_name(c)
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
                const = extract_string_value(vc)
        if name:
            out.append({
                "name": name,
                "type": type_name,
                "is_static_final": is_static_final,
                "const_value": const,
            })
    return out
