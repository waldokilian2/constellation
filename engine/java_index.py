"""
Java symbol index — the type-aware backbone for detection + call resolution.

The detector and call-graph builder were previously *name-based*: producers
matched on variable names (``rabbitTemplate``), calls resolved by camelCase
guesses, and channels were read only from string literals. That works on toy
repos but produces false positives/negatives on real Spring code, which wires
dependencies by interface type and externalizes topic/queue names to config.

This module scans every parsed ``*.java`` file across all repos once and
builds a symbol table:

  * classes/interfaces/enums/records with their package, imports, supertypes,
    declared fields (+ their types), and stereotype,
  * every method (name, param types, return type, AST node) for resolution,
  * ``public static final String`` constants (name → literal value),
  * application config (``application.properties`` / ``application.yml``) for
    resolving ``${placeholder}`` channel names.

It then answers the questions the detector/builder actually need:

  * what type does this field/variable resolve to?
  * which classes implement this interface? (so a call on an interface-typed
    field resolves to its impl)
  * resolve ``service.process(args)`` → a concrete method (import-aware),
  * resolve a channel token (literal / constant ref / ``${...}`` / ``#{...}``)
    to a real channel string.

Everything here stays deterministic and stdlib-only — no LLM, no execution.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from tree_sitter import Node

from .parser import JavaParser


@dataclass
class ClassInfo:
    repo: str
    simple_name: str
    fqn: str
    kind: str  # class | interface | enum | record
    file: str  # repo-relative
    line: int
    package: str
    imports_explicit: list[str] = field(default_factory=list)
    imports_wildcard: list[str] = field(default_factory=list)
    supertypes: list[str] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)  # name -> simple type
    node: Optional[Node] = None


@dataclass
class MethodInfo:
    repo: str
    class_simple: str
    name: str
    param_types: list[str]
    return_type: str
    file: str
    line: int
    node: Optional[Node] = None


# Framework types whose field/method calls mark a producer.
# Matched by the *declared field type*, not the variable name.
PRODUCER_TYPES: dict[str, set[str]] = {
    # KafkaTemplate.send(...) — every overload takes the topic as args[0].
    "KafkaTemplate": {"send"},
    "RabbitTemplate": {"convertAndSend", "convertSendAndReceive", "send"},
    "AmqpTemplate": {"convertAndSend", "convertSendAndReceive", "send"},
    "JmsTemplate": {"convertAndSend", "convertSendAndReceive", "send"},
    "ApplicationEventPublisher": {"publishEvent"},
    "StreamBridge": {"send"},  # Spring Cloud Stream
    "PulsarTemplate": {"send", "sendAsync"},  # Apache Pulsar (topic = first arg)
    "Connection": {"publish"},  # NATS / nats.java (subject = first arg)
}

EVENT_PUBLISHER_TYPES = {"ApplicationEventPublisher"}


# Field-type → HTTP client method set. RestTemplate is method-based; WebClient
# is fluent (get().uri(...).retrieve()) so we match the fluent entry calls and
# dig the URI out of the nested .uri(...) invocation.
#
# ⭐ Java-only scope: beyond the Spring big three we also cover the JDK's
# java.net.http.HttpClient (send/sendAsync with HttpRequest builder), OkHttp,
# JAX-RS Client/WebTarget (target().path()...request()), and Spring 6.1+
# RestClient (get()/post()/... fluent, same shape as WebClient). Feign is NOT
# here — Feign client interfaces are handled via @FeignClient in the detector.
HTTP_CLIENT_TYPES: dict[str, set[str]] = {
    "RestTemplate": {
        "getForObject", "getForEntity", "postForObject", "postForEntity",
        "put", "patchForObject", "delete", "exchange", "execute",
    },
    "WebClient": {"get", "post", "put", "patch", "delete", "exchange"},
    "RestClient": {"get", "post", "put", "patch", "delete"},  # Spring 6.1 fluent
    "HttpClient": {"send", "sendAsync", "execute"},   # java.net.http (send*) OR apache (execute)
    "OkHttpClient": {"newCall"},           # Request.Builder().url(...) — URI from nested builder
    "Client": {"target", "invoke"},        # JAX-RS — URI from .target("...")
    "WebTarget": {"request", "path"},
    # Apache HttpComponents (sync): execute(new HttpGet("http://...")); URI dug out of the
    # request's constructor, verb from the request class (HttpGet→GET).
    "CloseableHttpClient": {"execute"},
    "DefaultCloseableHttpClient": {"execute"},
    # Async HTTP client (async-http-client): prepareGet("http://...").execute();
    # URI is a direct string arg, verb encoded in the prepare* method name.
    "AsyncHttpClient": {
        "prepareGet", "preparePost", "preparePut", "preparePatch",
        "prepareDelete", "prepareHead", "prepareOptions", "execute",
    },
    "DefaultAsyncHttpClient": {
        "prepareGet", "preparePost", "preparePut", "preparePatch",
        "prepareDelete", "prepareHead", "prepareOptions", "execute",
    },
}


class JavaIndex:
    """Repo-wide symbol table + type-aware resolution."""

    def __init__(self):
        self.parser = JavaParser()
        self.by_simple: dict[str, list[ClassInfo]] = {}
        self.by_fqn: dict[str, ClassInfo] = {}
        self.methods: list[MethodInfo] = []
        # (class_simple, const_name) -> value; plus a flat fallback by name.
        self.constants: dict[tuple[str, str], str] = {}
        self.const_by_name: dict[str, str] = {}
        self.config: dict[str, str] = {}
        # interface simple name -> impl ClassInfos
        self._impls_cache: dict[str, list[ClassInfo]] = {}
        # (class_simple, method_name) -> resolved methods (incl. negatives), so the
        # supertype chain is walked at most once per (class, method) per index.
        self._hierarchy_cache: dict[tuple[str, str], list] = {}

    # ── build ──────────────────────────────────────────────────────

    def build(
        self,
        files: list[tuple[str, Path, Path]],
    ) -> None:
        """Index every parsed Java file.

        ``files`` is a list of ``(repo_name, repo_root, file_path)``. Files are
        parsed here (single source of truth for the whole run).
        """
        parsed: list[tuple[str, Path, Path, Node]] = []
        for repo_name, repo_root, file_path in files:
            root = self.parser.parse_file(file_path)
            if root is None:
                continue
            parsed.append((repo_name, repo_root, file_path, root))

        for repo_name, repo_root, file_path, root in parsed:
            self._index_file(repo_name, repo_root, file_path, root)

        # Load application config from all repos (properties + yml).
        roots_seen: set[Path] = set()
        for _repo, repo_root, _file in files:
            if repo_root in roots_seen:
                continue
            roots_seen.add(repo_root)
            self._load_config(repo_root)

    def _index_file(self, repo: str, repo_root: Path, file_path: Path, root: Node) -> None:
        try:
            rel = str(file_path.resolve().relative_to(repo_root.resolve()))
        except ValueError:
            rel = str(file_path)

        package = self.parser.get_package(root)
        expl, wild = self.parser.get_imports(root)

        for type_node, kind in self.parser.find_types(root):
            simple = self.parser.get_class_name(type_node)
            if not simple:
                continue
            fqn = f"{package}.{simple}" if package else simple
            supertypes = self.parser.get_supertypes(type_node)
            fields = {
                f["name"]: f["type"]
                for f in self.parser.get_fields(type_node)
                if f.get("name") and f.get("type")
            }
            # static final String constants
            for f in self.parser.get_fields(type_node):
                if f.get("is_static_final") and f.get("type") == "String" and f.get("const_value"):
                    self.constants[(simple, f["name"])] = f["const_value"]
                    self.const_by_name.setdefault(f["name"], f["const_value"])

            ci = ClassInfo(
                repo=repo, simple_name=simple, fqn=fqn, kind=kind, file=rel,
                line=type_node.start_point[0] + 1, package=package,
                imports_explicit=expl, imports_wildcard=wild,
                supertypes=supertypes, fields=fields, node=type_node,
            )
            self.by_simple.setdefault(simple, []).append(ci)
            self.by_fqn[fqn] = ci

            for m_node in self.parser.find_methods(type_node):
                sig = self.parser.get_method_signature(m_node)
                if not sig["name"]:
                    continue
                self.methods.append(MethodInfo(
                    repo=repo, class_simple=simple, name=sig["name"],
                    param_types=sig["param_types"], return_type=sig["return_type"],
                    file=rel, line=m_node.start_point[0] + 1, node=m_node,
                ))

    # ── config (application.properties / .yml) ─────────────────────

    def _load_config(self, repo_root: Path) -> None:
        candidates = []
        for pat in ("*.properties", "*.yml", "*.yaml"):
            candidates.extend(repo_root.rglob(f"**/application{pat}"))
            candidates.extend(repo_root.rglob(f"application{pat}"))
        for cfg in candidates:
            try:
                text = cfg.read_text(errors="replace")
            except OSError:
                continue
            if cfg.suffix == ".properties":
                self.config.update(self._parse_properties(text))
            else:
                self.config.update(self._parse_yaml(text))

    @staticmethod
    def _parse_properties(text: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
            elif ":" in line:
                k, _, v = line.partition(":")
            else:
                continue
            out[k.strip()] = v.strip()
        return out

    @staticmethod
    def _parse_yaml(text: str) -> dict[str, str]:
        """Minimal YAML flattener for the common nested-map case.

        Turns ``spring:\\n  kafka:\\n    bootstrap-servers: x`` into
        ``{"spring.kafka.bootstrap-servers": "x"}``. Lists and anchors are
        ignored; this only needs to resolve ``${...}`` channel keys.
        """
        out: dict[str, str] = {}
        stack: list[tuple[int, str]] = []  # (indent, key)
        for raw in text.splitlines():
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            line = raw.strip()
            if line.startswith("- "):
                continue  # skip list items
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            while stack and stack[-1][0] >= indent:
                stack.pop()
            if val:
                path = ".".join([k for _i, k in stack] + [key])
                out[path] = val
            else:
                stack.append((indent, key))
        return out

    # ── type resolution ────────────────────────────────────────────

    def find_class(self, ci: Optional[ClassInfo], simple: str) -> Optional[ClassInfo]:
        """Resolve a simple type name to a single indexed class (import-aware).

        Prefers a matching explicit import, then the same-package FQN, then a
        uniquely-named class. Returns None if ambiguous or unknown.
        """
        if not simple:
            return None
        # 1. explicit import
        if ci:
            for imp in ci.imports_explicit:
                if imp.rsplit(".", 1)[-1] == simple:
                    hit = self.by_fqn.get(imp)
                    if hit:
                        return hit
            # 2. wildcard import → look for a class whose fqn starts with pkg
            for wpkg in ci.imports_wildcard:
                cand = [c for c in self.by_simple.get(simple, []) if c.fqn.startswith(wpkg + ".")]
                if len(cand) == 1:
                    return cand[0]
        # 3. same package
        if ci and ci.package:
            same = self.by_fqn.get(f"{ci.package}.{simple}")
            if same:
                return same
        # 4. unique by simple name
        matches = self.by_simple.get(simple, [])
        if len(matches) == 1:
            return matches[0]
        return None

    def impls_of(self, interface_simple: str) -> list[ClassInfo]:
        if interface_simple in self._impls_cache:
            return self._impls_cache[interface_simple]
        out = [
            ci for ci in self.by_fqn.values()
            if interface_simple in ci.supertypes
        ]
        self._impls_cache[interface_simple] = out
        return out

    def field_type(self, ci: ClassInfo, field_name: str) -> str:
        """Declared type of a field, walking supertypes if not local."""
        if field_name in ci.fields:
            return ci.fields[field_name]
        for sup in ci.supertypes:
            sup_ci = self.find_class(ci, sup)
            if sup_ci and field_name in sup_ci.fields:
                return sup_ci.fields[field_name]
        return ""

    def resolve_receiver_type(
        self, ci: ClassInfo, receiver: str, local_types: dict[str, str],
    ) -> Optional[ClassInfo]:
        """Resolve a call receiver (field / local / static type) to a class."""
        if not receiver or receiver in ("this", "super"):
            return ci
        # local variable (best-effort: caller passes its types)
        if receiver in local_types:
            return self.find_class(ci, local_types[receiver])
        # field
        ft = self.field_type(ci, receiver)
        if ft:
            cls = self.find_class(ci, ft)
            if cls:
                return cls
            # field type might itself be an interface with impls
            if self.impls_of(ft):
                return None  # handled by resolve_call via impl lookup
        # static call: receiver is a class name
        if receiver in self.by_simple:
            matches = self.by_simple.get(receiver, [])
            if len(matches) == 1:
                return matches[0]
        return None

    def find_methods(self, class_simple: str, method_name: str) -> list[MethodInfo]:
        return [
            m for m in self.methods
            if m.name == method_name and m.class_simple == class_simple
        ]

    def find_methods_in_hierarchy(
        self, ci: ClassInfo, method_name: str, _seen: Optional[set[str]] = None,
    ) -> list[MethodInfo]:
        """Resolve a method up the supertype chain (transitive).

        ``find_methods`` only matches a method declared *directly* on a class
        by simple name. Real call edges often land on a base class (e.g.
        ``orderService.process()`` where ``process`` lives on an abstract
        ``BaseOrderService``). This walks ``ci`` → each resolved supertype
        (recursively, import-aware) until the method is found, returning the
        first non-empty level. Memoized per (class, method) so the chain is
        walked at most once per index; used only as a fallback, so direct hits
        keep their original behaviour and confidence.
        """
        key = (ci.simple_name, method_name)
        cached = self._hierarchy_cache.get(key)
        if cached is not None:
            return cached
        result = self._walk_hierarchy(ci, method_name, set())
        self._hierarchy_cache[key] = result
        return result

    def _walk_hierarchy(
        self, ci: ClassInfo, method_name: str, seen: set[str],
    ) -> list[MethodInfo]:
        if ci.simple_name in seen:
            return []
        seen.add(ci.simple_name)
        direct = self.find_methods(ci.simple_name, method_name)
        if direct:
            return direct
        for sup in ci.supertypes:
            sup_ci = self.find_class(ci, sup)
            if sup_ci:
                found = self._walk_hierarchy(sup_ci, method_name, seen)
                if found:
                    return found
        return []

    def class_by_loc(self, repo: str, file: str, simple: str) -> Optional[ClassInfo]:
        """ClassInfo for a class located at (repo, file, simple name)."""
        matches = self.by_simple.get(simple, [])
        for ci in matches:
            if ci.repo == repo and ci.file == file:
                return ci
        return matches[0] if matches else None

    def class_for_method(self, method: MethodInfo) -> Optional[ClassInfo]:
        return self.class_by_loc(method.repo, method.file, method.class_simple)

    def resolve_call(
        self,
        ci: ClassInfo,
        receiver: str,
        method_name: str,
        arity: Optional[int] = None,
        local_types: Optional[dict[str, str]] = None,
    ) -> tuple[Optional[MethodInfo], bool, Optional[str]]:
        """Resolve a method call to a definition.

        Returns ``(method, ambiguous, receiver_type_simple)``. ``ambiguous`` is
        True when the call could not be uniquely resolved (multiple candidates).
        Strategy: resolve receiver → type → (interface→impls or concrete class)
        → find the method there, arity-filtered when possible.
        """
        local_types = local_types or {}
        recv_type = ""

        # Determine receiver type simple name.
        if not receiver or receiver in ("this", "super"):
            recv_type = ci.simple_name
        elif receiver in local_types:
            recv_type = local_types[receiver]
        else:
            ft = self.field_type(ci, receiver)
            if ft:
                recv_type = ft
            elif receiver in self.by_simple and len(self.by_simple[receiver]) == 1:
                recv_type = receiver  # static call on a class name

        # Candidate classes to search.
        candidate_classes: list[ClassInfo] = []
        if recv_type:
            concrete = self.find_class(ci, recv_type)
            if concrete:
                if concrete.kind == "interface":
                    candidate_classes = self.impls_of(recv_type) or [concrete]
                else:
                    candidate_classes = [concrete]
            elif self.impls_of(recv_type):
                candidate_classes = self.impls_of(recv_type)

        # Gather candidate methods across candidate classes.
        cands: list[MethodInfo] = []
        for cc in candidate_classes:
            cands.extend(self.find_methods(cc.simple_name, method_name))

        # Fallback: if none of the candidate classes declare the method, walk
        # up the supertype chain of each (multi-level dispatch). This only adds
        # hits where the direct search found nothing, so direct resolutions
        # keep their original (non-ambiguous) behaviour.
        if not cands:
            for cc in candidate_classes:
                cands.extend(self.find_methods_in_hierarchy(cc, method_name))

        # Arity filter (when known and it narrows the set).
        if arity is not None and arity >= 0:
            narrowed = [m for m in cands if len(m.param_types) == arity]
            if narrowed:
                cands = narrowed

        if not cands:
            return None, False, recv_type or None

        if len(cands) == 1:
            return cands[0], False, recv_type or None

        # Multiple candidates — prefer one in the receiver's own class.
        own = [m for m in cands if any(cc.simple_name == m.class_simple for cc in candidate_classes)]
        # Prefer a unique class among candidates.
        classes = {m.class_simple for m in cands}
        if len(classes) == 1:
            # one class, multiple overloads — pick arity match or first
            return cands[0], False, recv_type or None
        return cands[0], True, recv_type or None

    # ── channel resolution ─────────────────────────────────────────

    def resolve_channel(self, token: Optional[str], ci: Optional[ClassInfo] = None) -> str:
        """Turn a channel token into a concrete channel string.

        Handles literals, ``Class.CONST`` / ``CONST`` references, ``${...}``
        placeholders (via application config), and ``#{...}`` SpEL (returned
        verbatim as a dynamic marker). Unknown tokens pass through unchanged.
        """
        if not token:
            return "unknown"
        t = token.strip()

        # SpEL #{...}
        if t.startswith("#{"):
            return t  # dynamic — won't cross-link, but preserved for display

        # Placeholder ${...} (optionally with :default)
        if "${" in t:
            inner = t[t.find("${") + 2: t.find("}") if "}" in t else len(t)]
            name, sep, default = inner.partition(":")
            if name in self.config:
                return self.config[name]
            for k, v in self.config.items():
                if k.endswith("." + name) or k == name:
                    return v
            return default if sep else t  # fall back to default or raw token

        # Constant ref: Class.NAME
        if "." in t and not t.startswith("/"):
            cls, _, cname = t.rpartition(".")
            if (cls, cname) in self.constants:
                return self.constants[(cls, cname)]
            if cname in self.const_by_name:
                return self.const_by_name[cname]

        # Bare constant name
        if t in self.const_by_name and not t.startswith("/"):
            return self.const_by_name[t]

        return t
