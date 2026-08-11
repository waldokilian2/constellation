"""Java EE / Jakarta EE entry-point detection.

Covers JAX-RS (@Path + verb annotations), CDI event observers (@Observes on a
parameter), EJB timers (@Schedule/@Schedules/@Timeout), Java EE WebSocket
(@ServerEndpoint handler methods), and JMS MessageDriven Beans (MDB).
"""
from __future__ import annotations
from tree_sitter import Node

from .base import FrameworkHandler, ScanContext
from ..models import EntryPoint, EntryPointType
from ..producers.jvm import REST_PATH_KEYS


# JAX-RS verbs (method level); the path comes from @Path (class + method).
JAXRS_VERB_ANN = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
# Java EE WebSocket handler methods on a @ServerEndpoint class.
EE_WS_HANDLER_ANN = {"OnMessage", "OnOpen", "OnClose", "OnError"}
# EJB timer methods.
EJB_SCHEDULE_ANN = {"Schedule", "Schedules", "Timeout"}


class JakartaHandler(FrameworkHandler):
    """Jakarta EE entry points: JAX-RS, CDI, EJB, WebSocket, MDB."""

    def begin_class(self, ctx: ScanContext) -> None:
        # @Path prefix and @ServerEndpoint path from class-level annotations.
        jaxrs_prefix = ""
        ee_ws_path = ""
        for ann in ctx.java.get_class_annotations(ctx.class_node):
            name = ctx.java.get_annotation_name(ann)
            args = ctx.java.get_annotation_args(ann)
            if name == "Path":
                for k in REST_PATH_KEYS:
                    if k in args and args[k]:
                        jaxrs_prefix = args[k][0]
                        break
            elif name == "ServerEndpoint":
                for k in REST_PATH_KEYS:
                    if k in args and args[k]:
                        ee_ws_path = args[k][0]
                        break
        ctx.jaxrs_prefix = jaxrs_prefix
        ctx.ee_ws_path = ee_ws_path

    def class_entries(self, ctx: ScanContext) -> list[EntryPoint]:
        # JMS MessageDriven Bean: one consumer whose channel is the
        # activationConfig destination. MDB implements MessageListener.onMessage.
        java = ctx.java
        for ann in java.get_class_annotations(ctx.class_node):
            if java.get_annotation_name(ann) != "MessageDriven":
                continue
            destination = ""
            for nested in java.find_nested_annotations(ann):
                if java.get_annotation_name(nested) != "ActivationConfigProperty":
                    continue
                props = java.get_annotation_args(nested)
                pname = (props.get("propertyName") or [""])[0]
                pvalue = (props.get("propertyValue") or [""])[0]
                if pname == "destination" and pvalue:
                    destination = pvalue
            channel = ctx.index.resolve_channel(destination or "unknown", ctx.ci)
            return [ctx.make_entry(ctx.class_node, "onMessage", channel, EntryPointType.JMS_CONSUMER, msg_type="javax.jms.Message")]
        return []

    def method_entries(self, ctx, m_node, m_name, annotations, params):
        java = ctx.java
        out: list[EntryPoint] = []
        ann_names = [java.get_annotation_name(a) for a in annotations]
        params_ann = java.get_method_params_annotated(m_node)
        jaxrs_prefix = getattr(ctx, "jaxrs_prefix", "")
        ee_ws_path = getattr(ctx, "ee_ws_path", "")

        # JAX-RS: method carries an HTTP-verb annotation; path = class @Path + method @Path.
        verb = next((n for n in ann_names if n in JAXRS_VERB_ANN), None)
        if verb:
            method_path = ""
            for a in annotations:
                if java.get_annotation_name(a) == "Path":
                    method_path = (java.get_annotation_args(a).get("_raw") or [""])[0]
                    break
            channel = ScanContext.join_rest_path(jaxrs_prefix, method_path) if method_path else (jaxrs_prefix or "unknown")
            out.append(ctx.make_entry(m_node, m_name, ctx.index.resolve_channel(channel, ctx.ci), EntryPointType.REST_ENDPOINT, method_type=verb))
            return out

        # CDI event observer: a parameter annotated @Observes → channel = event type.
        for p in params_ann:
            if "Observes" in p.get("annotations", []):
                evt = p["type"] or "unknown-event"
                out.append(ctx.make_entry(m_node, m_name, evt, EntryPointType.EVENT_LISTENER, msg_type=evt))
                return out

        # EJB timer: @Schedule / @Schedules.
        if any(n in EJB_SCHEDULE_ANN for n in ann_names):
            out.append(ctx.make_entry(m_node, m_name, f"@Schedule:{m_name}", EntryPointType.SCHEDULED_TASK))
            return out

        # Java EE WebSocket: handler methods on a @ServerEndpoint class.
        if ee_ws_path and any(n in EE_WS_HANDLER_ANN for n in ann_names):
            out.append(ctx.make_entry(m_node, m_name, ee_ws_path, EntryPointType.WEBSOCKET))
            return out

        return out
