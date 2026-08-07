"""
Entry point detector for Java Spring applications.

Scans Java source files for framework-specific annotations and method calls
that mark entry points into the application:

  @RabbitListener(queues = "name")     → RabbitMQ consumer
  @KafkaListener(topics = "name")      → Kafka consumer
  @JmsListener(destination = "name")   → JMS consumer
  @GetMapping("/path")                 → REST endpoint
  @PostMapping("/path")                → REST endpoint
  @PutMapping("/path")                 → REST endpoint
  @DeleteMapping("/path")              → REST endpoint
  @RequestMapping("/path")             → REST endpoint
  @EventListener                       → Spring event handler

Also detects producers:
  rabbitTemplate.convertAndSend(...)   → RabbitMQ producer
  kafkaTemplate.send(...)              → Kafka producer
  jmsTemplate.convertAndSend(...)      → JMS producer
  applicationEventPublisher.publishEvent(...) → Event publisher

The detection is 100% deterministic — annotation names and string arguments
are read directly from the AST.
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
from tree_sitter import Node

from .parser import JavaParser
from .models import (
    EntryPoint,
    EntryPointType,
    Producer,
    ProducerType,
    ClassMethod,
)


# ── Annotation → EntryPointType mapping ───────────────────────────

CONSUMER_ANNOTATIONS: dict[str, EntryPointType] = {
    "RabbitListener": EntryPointType.RABBITMQ_CONSUMER,
    "KafkaListener": EntryPointType.KAFKA_CONSUMER,
    "JmsListener": EntryPointType.JMS_CONSUMER,
    "SqsListener": EntryPointType.SQS_CONSUMER,
}

REST_ANNOTATIONS: dict[str, EntryPointType] = {
    "GetMapping": EntryPointType.REST_ENDPOINT,
    "PostMapping": EntryPointType.REST_ENDPOINT,
    "PutMapping": EntryPointType.REST_ENDPOINT,
    "DeleteMapping": EntryPointType.REST_ENDPOINT,
    "PatchMapping": EntryPointType.REST_ENDPOINT,
    "RequestMapping": EntryPointType.REST_ENDPOINT,
}

EVENT_ANNOTATIONS: dict[str, EntryPointType] = {
    "EventListener": EntryPointType.EVENT_LISTENER,
    "TransactionalEventListener": EntryPointType.EVENT_LISTENER,
}

# Combined: annotation name → (EntryPointType, channel_arg_key)
# The channel_arg_key tells us which annotation argument holds the channel name.
# "_raw" means the channel is the bare string value (no key=value pair).
ENTRY_ANNOTATIONS: dict[str, tuple[EntryPointType, str]] = {}
for name, ept in {**CONSUMER_ANNOTATIONS, **REST_ANNOTATIONS, **EVENT_ANNOTATIONS}.items():
    if name in CONSUMER_ANNOTATIONS:
        # queues = "..." or topics = "..." or destination = "..."
        arg_map = {
            "RabbitListener": "queues",
            "KafkaListener": "topics",
            "JmsListener": "destination",
            "SqsListener": "_raw",
        }
        ENTRY_ANNOTATIONS[name] = (ept, arg_map.get(name, "_raw"))
    elif name in REST_ANNOTATIONS:
        ENTRY_ANNOTATIONS[name] = (ept, "_raw")
    elif name in EVENT_ANNOTATIONS:
        # For @EventListener, the channel is the event type (parameter type)
        ENTRY_ANNOTATIONS[name] = (ept, "_param_type")

# ── Producer detection patterns ───────────────────────────────────

# receiver name → method name → ProducerType
# These match patterns like:
#   rabbitTemplate.convertAndSend("queue", msg)
#   kafkaTemplate.send("topic", msg)
#   jmsTemplate.convertAndSend("dest", msg)
PRODUCER_RECEIVERS: dict[str, dict[str, ProducerType]] = {
    "rabbitTemplate": {
        "convertAndSend": ProducerType.RABBITMQ_PRODUCER,
        "send": ProducerType.RABBITMQ_PRODUCER,
    },
    "amqpTemplate": {
        "convertAndSend": ProducerType.RABBITMQ_PRODUCER,
        "send": ProducerType.RABBITMQ_PRODUCER,
    },
    "kafkaTemplate": {
        "send": ProducerType.KAFKA_PRODUCER,
    },
    "template": {
        "send": ProducerType.KAFKA_PRODUCER,
    },
    "kafkaProducer": {
        "send": ProducerType.KAFKA_PRODUCER,
    },
    "jmsTemplate": {
        "convertAndSend": ProducerType.JMS_PRODUCER,
        "send": ProducerType.JMS_PRODUCER,
    },
    "applicationEventPublisher": {
        "publishEvent": ProducerType.EVENT_PUBLISHER,
    },
    "eventPublisher": {
        "publishEvent": ProducerType.EVENT_PUBLISHER,
    },
}

# For event publishers, the channel is a type name from `new EventType()`
# rather than a string literal. We extract the constructor name.
EVENT_PUBLISHER_RECEIVERS = {
    "applicationEventPublisher",
    "eventPublisher",
}


class EntryPointDetector:
    """Detects entry points and producers in Java source files."""

    def __init__(self, repo_name: str):
        self.repo = repo_name
        self.parser = JavaParser()

    def detect_in_file(self, file_path: Path, repo_root: Optional[Path] = None) -> tuple[list[EntryPoint], list[Producer], list[ClassMethod]]:
        """
        Parse a single Java file and return:
        - entry points found
        - producers found
        - all methods indexed (for call graph resolution)
        """
        root = self.parser.parse_file(file_path)
        if not root:
            return [], [], []

        # Store repo-relative paths so the graph is portable across mounts.
        if repo_root is not None:
            try:
                relative_path = str(file_path.resolve().relative_to(repo_root.resolve()))
            except ValueError:
                relative_path = str(file_path)
        else:
            relative_path = str(file_path)
        entry_points: list[EntryPoint] = []
        producers: list[Producer] = []
        methods: list[ClassMethod] = []

        # Find all classes in the file
        classes = self.parser.find_classes(root)

        for class_node in classes:
            class_name = self.parser.get_class_name(class_node)
            if not class_name:
                continue

            # Check for class-level @RequestMapping prefix
            class_annotations = self.parser.get_class_annotations(class_node)
            path_prefix = ""
            for ann in class_annotations:
                ann_name = self.parser.get_annotation_name(ann)
                if ann_name == "RequestMapping":
                    args = self.parser.get_annotation_args(ann)
                    path_prefix = args.get("_raw", args.get("value", ""))
                    break

            # Index all methods in this class
            method_nodes = self.parser.find_methods(class_node)
            for m_node in method_nodes:
                m_name = self.parser.get_method_name(m_node)
                if m_name:
                    methods.append(ClassMethod(
                        name=m_name,
                        class_name=class_name,
                        file=relative_path,
                        line=m_node.start_point[0] + 1,
                        node=m_node,
                    ))

            # Check for entry points
            for m_node in method_nodes:
                m_name = self.parser.get_method_name(m_node)
                annotations = self.parser.get_method_annotations(m_node)
                params = self.parser.get_method_parameters(m_node)

                for ann in annotations:
                    ann_name = self.parser.get_annotation_name(ann)
                    if ann_name in ENTRY_ANNOTATIONS:
                        ep_type, channel_key = ENTRY_ANNOTATIONS[ann_name]
                        channel = self._extract_channel(
                            ann, channel_key, params
                        )
                        # Prepend class-level path prefix for REST endpoints
                        if ep_type == EntryPointType.REST_ENDPOINT and path_prefix:
                            if channel == "unknown":
                                channel = path_prefix
                            elif not channel.startswith("/"):
                                channel = f"{path_prefix}/{channel}"
                            else:
                                channel = f"{path_prefix}{channel}"

                        ep = EntryPoint(
                            id=f"{self.repo}:{class_name}.{m_name}",
                            repo=self.repo,
                            type=ep_type,
                            channel=channel,
                            class_name=class_name,
                            method=m_name,
                            file=relative_path,
                            line=m_node.start_point[0] + 1,
                            message_type=params[0]["type"] if params else "",
                        )
                        entry_points.append(ep)

                # Check for producers within method body
                body = self.parser.get_method_body(m_node)
                if body:
                    for inv in self.parser.find_method_invocations(body):
                        inv_parsed = self.parser.parse_method_invocation(inv)
                        receiver = inv_parsed["receiver"]
                        method_called = inv_parsed["method"]
                        args = inv_parsed["args"]

                        if receiver in PRODUCER_RECEIVERS:
                            receiver_methods = PRODUCER_RECEIVERS[receiver]
                            if method_called in receiver_methods:
                                prod_type = receiver_methods[method_called]
                                channel = args[0] if args else ""

                                # For event publishers, the channel is a type name
                                if receiver in EVENT_PUBLISHER_RECEIVERS:
                                    channel = self._extract_event_type_from_args(inv)
                                    if not channel and args:
                                        channel = args[0]

                                prod = Producer(
                                    id=f"{self.repo}:{class_name}.{m_name}:{method_called}",
                                    repo=self.repo,
                                    type=prod_type,
                                    channel=channel,
                                    method=f"{class_name}.{m_name}",
                                    file=relative_path,
                                    line=inv.start_point[0] + 1,
                                    message_type=self._extract_message_type_from_invocation(inv),
                                )
                                producers.append(prod)

        return entry_points, producers, methods

    def _extract_channel(
        self,
        annotation: Node,
        channel_key: str,
        method_params: list[dict],
    ) -> str:
        """Extract the channel name from an annotation or method params."""
        args = self.parser.get_annotation_args(annotation)

        if channel_key == "_param_type":
            # For @EventListener, channel = first param's type
            if method_params:
                return method_params[0]["type"]
            return "unknown-event"

        if channel_key in args:
            return args[channel_key]

        if "_raw" in args:
            return args["_raw"]

        return "unknown"

    def _extract_event_type_from_args(self, invocation: Node) -> str:
        """
        For publishEvent(new OrderCreatedEvent(...)), extract 'OrderCreatedEvent'.
        """
        for child in invocation.children:
            if child.type == "argument_list":
                for arg in child.children:
                    if arg.type == "object_creation_expression":
                        for oc in arg.children:
                            if oc.type == "type_identifier":
                                return oc.text.decode()
        return ""

    def _extract_message_type_from_invocation(self, invocation: Node) -> str:
        """Try to extract the message type from the invocation arguments."""
        inv = self.parser.parse_method_invocation(invocation)
        for arg in inv.get("args", []):
            # If arg looks like a class name (starts with uppercase)
            if arg and arg[0].isupper():
                return arg
        return ""

    def scan_directory(self, root_dir: Path) -> tuple[list[EntryPoint], list[Producer], list[ClassMethod]]:
        """
        Recursively scan a directory for Java files and detect all
        entry points and producers.
        """
        all_entries: list[EntryPoint] = []
        all_producers: list[Producer] = []
        all_methods: list[ClassMethod] = []

        java_files = sorted(root_dir.rglob("*.java"))

        for java_file in java_files:
            # Skip test files for now
            if "/test/" in str(java_file) or "/Test" in java_file.name:
                continue

            entries, producers, methods = self.detect_in_file(java_file, repo_root=root_dir)
            all_entries.extend(entries)
            all_producers.extend(producers)
            all_methods.extend(methods)

        return all_entries, all_producers, all_methods
