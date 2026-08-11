"""Extra framework entry points (Tier 1 / 2).

Detection beyond Spring/Jakarta annotations, all deterministic and read from
annotations or interface contracts:

  * ``public static void main(String[])`` (JVM entry)
  * lifecycle hooks (``@PostConstruct``/``@PreDestroy``, and interface contracts
    ``CommandLineRunner``/``ApplicationRunner``/``InitializingBean``/…)
  * Servlet API (``@WebServlet`` doX verbs, ``@WebFilter`` doFilter)
  * SOAP (JAX-WS ``@WebMethod`` on a ``@WebService`` class)
  * Spring for GraphQL (``@QueryMapping``/``@MutationMapping``/…)
  * gRPC (service methods on an ``*ImplBase`` subclass, requiring a
    ``StreamObserver`` parameter)
  * Spring Cloud Function (``@Bean`` returning ``Function``/``Supplier``/``Consumer``)
"""
from __future__ import annotations
from tree_sitter import Node

from .base import FrameworkHandler, ScanContext
from ..models import EntryPoint, EntryPointType
from ..languages import java_ast


# Lifecycle / startup hooks (method annotations).
LIFECYCLE_ANN = {"PostConstruct", "PreDestroy"}
# Interface contract (simple name in supertypes) → the triggering method name.
LIFECYCLE_IFACE_METHODS: dict[str, str] = {
    "CommandLineRunner": "run",
    "ApplicationRunner": "run",
    "InitializingBean": "afterPropertiesSet",
    "SmartInitializingSingleton": "afterSingletonsInstantiated",
    "DisposableBean": "destroy",
}

# Servlet API: @WebServlet class → one entry per doX verb found.
SERVLET_VERB_BY_METHOD = {
    "doGet": "GET", "doPost": "POST", "doPut": "PUT",
    "doDelete": "DELETE", "doPatch": "PATCH", "doHead": "HEAD", "doOptions": "OPTIONS",
    "service": "",
}

# JAX-WS SOAP: class @WebService + method @WebMethod.
SOAP_METHOD_ANN = {"WebMethod"}

# Spring for GraphQL — annotation → GraphQL operation kind (for method_type).
GRAPHQL_ANN: dict[str, str] = {
    "QueryMapping": "Query",
    "MutationMapping": "Mutation",
    "SubscriptionMapping": "Subscription",
    "SchemaMapping": "Schema",
    "BatchMapping": "Batch",
}

# Spring Cloud Function: @Bean returning Function/Supplier/Consumer.
CLOUD_FUNCTION_TYPES = {"Function", "Supplier", "Consumer"}


class ExtraHandler(FrameworkHandler):
    """Tier 1/2 entry points: main, lifecycle, servlet, SOAP, GraphQL, gRPC, Cloud Function."""

    def begin_class(self, ctx: ScanContext) -> None:
        java = ctx.java
        class_anns = java.get_class_annotations(ctx.class_node)
        # Servlet paths from @WebServlet / @WebFilter (class level).
        servlet_paths: list[str] = []
        is_web_filter = False
        is_webservice = any(java.get_annotation_name(a) == "WebService" for a in class_anns)
        for ann in class_anns:
            name = java.get_annotation_name(ann)
            if name in ("WebServlet", "WebFilter"):
                if name == "WebFilter":
                    is_web_filter = True
                aargs = java.get_annotation_args(ann)
                for k in ("urlPatterns", "value", "_raw"):
                    if aargs.get(k):
                        servlet_paths = aargs[k]
                        break
        if servlet_paths:
            servlet_paths = [ctx.index.resolve_channel(p, ctx.ci) or p for p in servlet_paths]
        # gRPC: class extends a generated *ImplBase → service name is the base minus "ImplBase".
        grpc_service = next(
            (s[:-len("ImplBase")] for s in ctx.ci.supertypes if s.endswith("ImplBase")),
            "",
        )
        ctx.is_webservice = is_webservice
        ctx.is_web_filter = is_web_filter
        ctx.servlet_paths = servlet_paths
        ctx.grpc_service = grpc_service

    def method_entries(self, ctx, m_node, m_name, annotations, params):
        java = ctx.java
        out: list[EntryPoint] = []
        ann_names = [java.get_annotation_name(a) for a in annotations]
        n_params = len(params)

        # ── public static void main(String[]) ──
        # A Spring Boot bootstrap main() (just SpringApplication.run(...)) is
        # framework boilerplate, not an architectural entry point — skip it so
        # per-service ``main`` noise doesn't clutter the graph. Genuine
        # main-entry apps (CLIs, standalone workers) are still detected.
        if m_name == "main" and self._is_static(m_node) and n_params == 1:
            fparams = next((c for c in m_node.children if c.type == "formal_parameters"), None)
            fparams_text = fparams.text.decode() if fparams is not None else ""
            ret = java.get_method_return_type(m_node)
            if ("String[]" in fparams_text or "String..." in fparams_text) and ret == "void":
                if not self._is_spring_boot_bootstrap(m_node):
                    out.append(ctx.make_entry(m_node, m_name, "main", EntryPointType.MAIN))
                return out

        # ── Lifecycle: @PostConstruct / @PreDestroy ──
        lc = next((n for n in ann_names if n in LIFECYCLE_ANN), None)
        if lc:
            out.append(ctx.make_entry(m_node, m_name, f"@{lc}:{m_name}", EntryPointType.LIFECYCLE))
            return out

        # ── Lifecycle: interface contract (CommandLineRunner.run, …) ──
        for iface, trigger in LIFECYCLE_IFACE_METHODS.items():
            if iface in ctx.ci.supertypes and m_name == trigger:
                out.append(ctx.make_entry(m_node, m_name, f"@{iface}:{trigger}", EntryPointType.LIFECYCLE))
                return out

        # ── SOAP: @WebMethod on a @WebService class ──
        if getattr(ctx, "is_webservice", False) and any(n in SOAP_METHOD_ANN for n in ann_names):
            op = m_name
            for a in annotations:
                if java.get_annotation_name(a) == "WebMethod":
                    op = (java.get_annotation_args(a).get("operationName") or [m_name])[0]
                    break
            out.append(ctx.make_entry(m_node, m_name, op, EntryPointType.SOAP_SERVICE, method_type="SOAP"))
            return out

        # ── GraphQL: @QueryMapping / @MutationMapping / … ──
        gql = next((n for n in ann_names if n in GRAPHQL_ANN), None)
        if gql:
            gargs = next(
                (java.get_annotation_args(a) for a in annotations if java.get_annotation_name(a) == gql), {}
            )
            name = (gargs.get("name") or gargs.get("value") or gargs.get("_raw") or [m_name])[0]
            msg_type = params[0]["type"] if params else ""
            out.append(ctx.make_entry(m_node, m_name, name, EntryPointType.GRAPHQL, msg_type=msg_type, method_type=GRAPHQL_ANN[gql]))
            return out

        # ── Servlet API: @WebServlet doX / @WebFilter doFilter ──
        sp = getattr(ctx, "servlet_paths", [])
        if sp:
            if getattr(ctx, "is_web_filter", False) and m_name == "doFilter":
                for pth in sp:
                    out.append(ctx.make_entry(m_node, m_name, pth, EntryPointType.SERVLET, method_type="FILTER"))
                return out
            if m_name in SERVLET_VERB_BY_METHOD:
                verb = SERVLET_VERB_BY_METHOD[m_name]
                for pth in sp:
                    out.append(ctx.make_entry(m_node, m_name, pth, EntryPointType.SERVLET, method_type=verb))
                return out

        # ── gRPC: @Override service methods on an *ImplBase subclass ──
        svc = getattr(ctx, "grpc_service", "")
        if svc and "Override" in ann_names and any(
            "StreamObserver" in (p.get("type") or "") for p in params
        ):
            msg_type = next((p["type"] for p in params if "StreamObserver" not in (p["type"] or "")), "")
            out.append(ctx.make_entry(m_node, m_name, f"/{svc}/{m_name}", EntryPointType.GRPC_SERVICE, msg_type=msg_type, method_type="GRPC"))
            return out

        # ── Spring Cloud Function: @Bean returning Function/Supplier/Consumer ──
        if "Bean" in ann_names:
            ret = java.get_method_return_type(m_node) or ""
            head = ret.split("<", 1)[0].strip()
            if head in CLOUD_FUNCTION_TYPES:
                bean = m_name
                for a in annotations:
                    if java.get_annotation_name(a) == "Bean":
                        ba = java.get_annotation_args(a)
                        bean = (ba.get("name") or ba.get("value") or ba.get("_raw") or [m_name])[0]
                        break
                msg_type = self._first_generic_arg(ret) if head in ("Function", "Consumer") else ""
                out.append(ctx.make_entry(m_node, m_name, bean, EntryPointType.CLOUD_FUNCTION, msg_type=msg_type, method_type=head.upper()))
                return out

        return out

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _is_spring_boot_bootstrap(m_node: Node) -> bool:
        """True when a main() body is just a Spring Boot launcher.

        Detects ``SpringApplication.run(...)`` / ``SpringApplicationBuilder``
        bootstrap calls so per-service ``main`` boilerplate is not reported as
        an architectural entry point.
        """
        body = java_ast.get_method_body(m_node)
        if body is None:
            return False
        for inv in java_ast.find_method_invocations(body):
            parsed = java_ast.parse_method_invocation(inv)
            if parsed["method"] == "run" and parsed["receiver"] in (
                "SpringApplication", "SpringApplicationBuilder"
            ):
                return True
            # Builder chain: new SpringApplicationBuilder(App.class).run(args).
            if parsed["method"] == "run" and "SpringApplicationBuilder" in (
                body.text.decode() or ""
            ):
                return True
        return False

    @staticmethod
    def _is_static(method_node: Node) -> bool:
        for c in method_node.children:
            if c.type == "modifiers":
                return "static" in c.text.decode().split()
        return False

    @staticmethod
    def _first_generic_arg(type_text: str) -> str:
        """``Function<OrderIn, OrderOut>`` → ``OrderIn`` (input type)."""
        if "<" in type_text and ">" in type_text:
            inner = type_text[type_text.find("<") + 1:type_text.rfind(">")]
            return inner.split(",", 1)[0].split("<", 1)[0].strip()
        return ""
