"""
Data models for Constellation engine.
Pure dataclasses — no logic, no dependencies.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import json


class EntryPointType(Enum):
    RABBITMQ_CONSUMER = "rabbitmq-consumer"
    KAFKA_CONSUMER = "kafka-consumer"
    JMS_CONSUMER = "jms-consumer"
    SQS_CONSUMER = "sqs-consumer"
    REST_ENDPOINT = "rest-endpoint"
    EVENT_LISTENER = "event-listener"
    SCHEDULED_TASK = "scheduled-task"
    WEBSOCKET = "websocket"
    # ── additional entry kinds (deterministic AST detection) ──
    SERVLET = "servlet"            # @WebServlet / @WebFilter (Servlet API)
    SOAP_SERVICE = "soap-service"  # JAX-WS @WebService / @WebMethod
    GRAPHQL = "graphql"            # @QueryMapping / @MutationMapping / @SchemaMapping ...
    GRPC_SERVICE = "grpc-service"  # class extends *ImplBase (generated gRPC base)
    LIFECYCLE = "lifecycle"        # @PostConstruct / CommandLineRunner / ApplicationRunner / InitializingBean / @WebListener
    MAIN = "main"                  # public static void main(String[])
    CLOUD_FUNCTION = "cloud-function"  # @Bean Function/Supplier/Consumer (Spring Cloud Function)
    UNKNOWN = "unknown"


class ProducerType(Enum):
    RABBITMQ_PRODUCER = "rabbitmq-producer"
    KAFKA_PRODUCER = "kafka-producer"
    JMS_PRODUCER = "jms-producer"
    EVENT_PUBLISHER = "event-publisher"
    PULSAR_PRODUCER = "pulsar-producer"
    NATS_PRODUCER = "nats-producer"
    HTTP_CALL = "http-call"  # sync HTTP request (Feign/RestTemplate/WebClient/...)
    UNKNOWN = "unknown"


class ConfidenceTag(Enum):
    """Confidence of a call-tree edge — the single source of truth for the
    vocabulary. String values are part of the serialised graph.json contract
    (asserted by the test suite) and must not change.

    * ``EXTRACTED`` — call resolved to a concrete definition.
    * ``INFERRED``  — call name matched but the target could not be confirmed.
    * ``AMBIGUOUS`` — multiple possible targets; the first one was chosen.
    * ``TRUNCATED`` — the per-tree node cap was hit during traversal.
    """
    EXTRACTED = "EXTRACTED"
    INFERRED = "INFERRED"
    AMBIGUOUS = "AMBIGUOUS"
    TRUNCATED = "TRUNCATED"


@dataclass
class CallNode:
    """A single node in a call tree — a function/method call and its children."""
    method: str
    file: str = ""
    line: int = 0
    class_name: str = ""
    children: list[CallNode] = field(default_factory=list)
    confidence: str = ConfidenceTag.EXTRACTED.value  # ConfidenceTag value

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "file": self.file,
            "line": self.line,
            "class_name": self.class_name,
            "confidence": self.confidence,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class EntryPoint:
    """An entry point into the application — a message handler, REST endpoint, etc."""
    id: str
    repo: str
    type: EntryPointType
    channel: str  # queue name, topic name, URL path, or event type
    class_name: str
    method: str
    file: str
    line: int
    message_type: str = ""  # parameter type (e.g., "OrderMessage")
    method_type: str = ""  # HTTP verb for REST endpoints (e.g., "GET", "POST"); "" when n/a
    call_tree: Optional[CallNode] = None
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "repo": self.repo,
            "type": self.type.value,
            "channel": self.channel,
            "class_name": self.class_name,
            "method": self.method,
            "file": self.file,
            "line": self.line,
            "message_type": self.message_type,
            "method_type": self.method_type,
            "call_tree": self.call_tree.to_dict() if self.call_tree else None,
            "metrics": self.metrics,
        }


@dataclass
class Producer:
    """A method that sends messages to a queue/topic/event bus."""
    id: str
    repo: str
    type: ProducerType
    channel: str
    method: str
    file: str
    line: int
    message_type: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "repo": self.repo,
            "type": self.type.value,
            "channel": self.channel,
            "method": self.method,
            "file": self.file,
            "line": self.line,
            "message_type": self.message_type,
        }


@dataclass
class CrossRepoLink:
    """A connection between two repos via a shared message channel or HTTP call."""
    channel: str
    producers: list[str] = field(default_factory=list)  # producer IDs
    consumers: list[str] = field(default_factory=list)  # entry point IDs
    kind: str = "message"  # "message" | "http"
    verb: str = ""         # HTTP method when kind == "http" and known

    def to_dict(self) -> dict:
        return {
            "channel": self.channel,
            "producers": self.producers,
            "consumers": self.consumers,
            "kind": self.kind,
            "verb": self.verb,
        }


@dataclass
class ConstellationGraph:
    """The full output graph — everything the engine produces."""
    repos: list[str] = field(default_factory=list)
    repo_roots: dict[str, str] = field(default_factory=dict)
    entry_points: list[EntryPoint] = field(default_factory=list)
    producers: list[Producer] = field(default_factory=list)
    cross_repo_links: list[CrossRepoLink] = field(default_factory=list)
    generated_at: str = ""
    engine_version: str = "0.1.0"

    def to_dict(self) -> dict:
        return {
            "repos": self.repos,
            "repo_roots": self.repo_roots,
            "entry_points": [ep.to_dict() for ep in self.entry_points],
            "producers": [p.to_dict() for p in self.producers],
            "cross_repo_links": [l.to_dict() for l in self.cross_repo_links],
            "generated_at": self.generated_at,
            "engine_version": self.engine_version,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
