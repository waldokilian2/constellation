"""Axon Framework (CQRS / Event Sourcing) consumer detection.

Axon routes commands/events/queries by *payload type* — the same model as the
in-house bus facade (:mod:`engine.frameworks.messagebus`): a handler method's
first parameter names the message it consumes, which is the same key the
producer side uses (``CommandGateway.send(payload)`` etc., detected in
:mod:`engine.producers.jvm` under ``PAYLOAD_ROUTED_TYPES``). The channel is
therefore the payload type, **FQN-resolved** so services that import the same
shared command/event library join on one key even when sibling services
declare same-simple-named messages in different packages.

Annotation → entry kind:

* ``@CommandHandler``  → ``MESSAGE_HANDLER`` (command consumer)
* ``@QueryHandler``    → ``MESSAGE_HANDLER`` (query consumer)
* ``@EventHandler``    → ``EVENT_LISTENER``  (event consumer)
* ``@EventSourcingHandler`` → ``EVENT_LISTENER`` (aggregate replay — same
  routing key as the event it sources)
"""
from __future__ import annotations
from tree_sitter import Node

from .base import FrameworkHandler, ScanContext
from ..models import EntryPoint, EntryPointType
from .messagebus import BUS_HANDLER_PARAM_SKIP

AXON_HANDLER_ANN: dict[str, EntryPointType] = {
    "CommandHandler": EntryPointType.MESSAGE_HANDLER,
    "QueryHandler": EntryPointType.MESSAGE_HANDLER,
    "EventHandler": EntryPointType.EVENT_LISTENER,
    "EventSourcingHandler": EntryPointType.EVENT_LISTENER,
}


class AxonHandler(FrameworkHandler):
    """``@CommandHandler``/``@EventHandler``/``@QueryHandler`` methods."""

    def method_entries(
        self, ctx: ScanContext, m_node: Node, m_name: str,
        annotations: list[Node], params: list[dict],
    ) -> list[EntryPoint]:
        out: list[EntryPoint] = []
        for ann in annotations:
            ep_type = AXON_HANDLER_ANN.get(ctx.java.get_annotation_name(ann))
            if ep_type is None:
                continue
            # Channel = the routed message type: first non-envelope parameter.
            msg_type = ""
            for p in params:
                t = (p.get("type") or "").rsplit(".", 1)[-1]
                if t and t not in BUS_HANDLER_PARAM_SKIP:
                    msg_type = t
                    break
            if not msg_type and params:
                msg_type = (params[0]["type"] or "").rsplit(".", 1)[-1]
            resolved = ctx.index.resolve_fqn(ctx.ci, msg_type) or msg_type
            out.append(ctx.make_entry(
                m_node, m_name, resolved, ep_type, msg_type=resolved,
            ))
        return out
