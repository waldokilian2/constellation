"""
Producer + sync-HTTP-call detection, organised as a swappable module.

Producer knowledge is **data + a small detector**, not welded into the entry
detector. Today this is the JVM backend (Spring templates, Pulsar, NATS, Feign,
RestTemplate/WebClient/…, Apache HttpComponents, async-http-client, STOMP,
RocketMQ, AWS SQS/SNS, JMS 2.0, MQTT, Redis pub/sub, Axon-style gateways, gRPC
client stubs). Adding a new ecosystem (e.g. a Python backend) is a sibling
module, not an edit to this one.

The :class:`JvmProducerDetector` holds the tables and the per-invocation
detection logic; the entry detector delegates body scanning to it.
"""
from .jvm import (
    JvmProducerDetector,
    PRODUCER_TYPES,
    PAYLOAD_ROUTED_TYPES,
    EVENT_PUBLISHER_TYPES,
    HTTP_CLIENT_TYPES,
    PRODUCER_TYPE_LABEL,
    FEIGN_ANNOTATIONS,
    FEIGN_ANNOTATION,
    REST_VERB_BY_ANN,
    REST_PATH_KEYS,
    GRPC_STUB_SUFFIXES,
)

__all__ = [
    "JvmProducerDetector",
    "PRODUCER_TYPES",
    "PAYLOAD_ROUTED_TYPES",
    "EVENT_PUBLISHER_TYPES",
    "HTTP_CLIENT_TYPES",
    "PRODUCER_TYPE_LABEL",
    "FEIGN_ANNOTATIONS",
    "FEIGN_ANNOTATION",
    "REST_VERB_BY_ANN",
    "REST_PATH_KEYS",
    "GRPC_STUB_SUFFIXES",
]
