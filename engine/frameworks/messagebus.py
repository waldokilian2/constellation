"""In-house message-bus handler detection.

Estates that route messaging through a custom bus facade (``bus.send(payload)``
— see :mod:`engine.producers.jvm` for the producer side) typically register
consumers with a small annotation pair: a marker on the class (``@MessageHandler``,
``@Consumer``, …) and a per-message annotation on the method (``@Handle``,
``@OnMessage``, …). The bus dispatches by *payload type*: the handler method's
first parameter names the message it consumes, which is the same key the
producer side uses as its channel — so the pair cross-links without any
queue-name configuration in code.

The vocabulary is deliberately broad (any of these marker/method names), since
in-house annotations are by definition not standardized. Detection stays
deterministic: both annotations must be present, and the channel is the
resolved parameter type — never a guess.
"""
from __future__ import annotations
from tree_sitter import Node

from .base import FrameworkHandler, ScanContext
from ..models import EntryPoint, EntryPointType
from ..producers.jvm import BUS_HANDLER_CLASS_ANN, BUS_HANDLER_METHOD_ANN

# Handler params that wrap or accompany the payload — never the routing key.
BUS_HANDLER_PARAM_SKIP = ("MessageContext", "Message", "byte[]", "String")


def _payload_simple_name(param_type: str) -> str:
    """Payload type from a handler parameter's declared type.

    Unwraps envelope generics first — buses commonly hand the handler a
    ``ContextualizedMessage<Payload>`` while still routing on ``Payload``.
    """
    t = (param_type or "").strip()
    if "<" in t and ">" in t:
        t = t[t.index("<") + 1: t.rindex(">")]
    return t.rsplit(".", 1)[-1]


class MessageBusHandler(FrameworkHandler):
    """``@MessageHandler``-style classes: ``@Handle m(Payload p)`` consumers."""

    def begin_class(self, ctx: ScanContext) -> None:
        names = {
            ctx.java.get_annotation_name(a)
            for a in ctx.java.get_class_annotations(ctx.class_node)
        }
        ctx.bus_handler_class = bool(names & BUS_HANDLER_CLASS_ANN)

    def method_entries(
        self, ctx: ScanContext, m_node: Node, m_name: str,
        annotations: list[Node], params: list[dict],
    ) -> list[EntryPoint]:
        if not getattr(ctx, "bus_handler_class", False):
            return []
        ann_names = {ctx.java.get_annotation_name(a) for a in annotations}
        if not ann_names & BUS_HANDLER_METHOD_ANN:
            return []
        # Channel = the message type: the payload parameter. Prefer the first
        # non-JDK parameter (skip MessageContext-style second args when the
        # payload is first anyway); fall back to params[0].
        msg_type = ""
        for p in params:
            t = (p.get("type") or "").rsplit(".", 1)[-1]
            if t and t not in BUS_HANDLER_PARAM_SKIP:
                msg_type = _payload_simple_name(t)
                break
        if not msg_type:
            msg_type = _payload_simple_name((params[0]["type"] if params else "")) or "unknown"
        # FQN-keyed channel: sibling repos declare same-simple-named messages
        # in different packages; a simple-name channel would falsely link them.
        resolved = ctx.index.resolve_fqn(ctx.ci, msg_type) or msg_type
        return [ctx.make_entry(
            m_node, m_name, resolved,
            EntryPointType.MESSAGE_HANDLER, msg_type=resolved,
        )]
