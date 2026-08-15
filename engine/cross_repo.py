"""
Cross-repo linker — finds connections between repos via shared channels.

Three kinds of edges:

* **message** — a producer in one repo sends to a queue/topic/event that a
  consumer in another repo listens on (exact channel-name match).
* **http** (sync calls) — a ``ProducerType.HTTP_CALL`` in one repo calls a
  REST endpoint in another repo. Paths are matched in **normalized template
  form** (``/api/orders/123`` == ``/api/orders/{id}``) and the link records
  the HTTP verb when both sides know it.
* **grpc** — a ``ProducerType.GRPC_CALL`` (generated ``*Stub`` invocation) in
  one repo calls a ``GRPC_SERVICE`` entry in another. Both sides use the same
  canonical ``/Service/method`` channel format, so the match is exact.
"""
from __future__ import annotations
from . import http_paths
from .models import (
    EntryPoint,
    EntryPointType,
    Producer,
    ProducerType,
    CrossRepoLink,
)


# Entry kinds whose ``channel`` is a real broker destination / event and may
# legitimately match a producer channel in the message pass. Non-broker entry
# kinds (REST/SOAP/GraphQL/gRPC/servlet/lifecycle/main/cloud-function/
# scheduled-task) use synthetic/semantic channels and are excluded so a GraphQL
# operation named "orders" doesn't link to a Kafka topic named "orders".
MESSAGE_CONSUMER_TYPES = {
    EntryPointType.RABBITMQ_CONSUMER,
    EntryPointType.KAFKA_CONSUMER,
    EntryPointType.JMS_CONSUMER,
    EntryPointType.SQS_CONSUMER,
    EntryPointType.PULSAR_CONSUMER,
    EntryPointType.MQTT_CONSUMER,
    EntryPointType.EVENT_LISTENER,
    EntryPointType.WEBSOCKET,
    EntryPointType.MESSAGE_HANDLER,  # in-house bus: channel = payload type
    EntryPointType.REACTIVE_INCOMING,  # SmallRye @Incoming (Quarkus)
}


class CrossRepoLinker:
    """Links repos by matching producer channels to consumer channels."""

    def link(
        self,
        entry_points: list[EntryPoint],
        producers: list[Producer],
    ) -> list[CrossRepoLink]:
        """
        Find all cross-repo links.

        For each channel name, find all producers and consumers.
        A link exists if at least one producer AND one consumer reference
        the same channel name.
        """
        links: list[CrossRepoLink] = []

        # ── message pass: exact channel matching (async/queue edges) ──
        # Build channel index
        channels: dict[str, dict] = {}

        for ep in entry_points:
            if ep.type not in MESSAGE_CONSUMER_TYPES:
                continue  # non-broker channels must not collide with broker topics
            ch = ep.channel
            if ch and ch != "unknown" and ch != "unknown-event":
                if ch not in channels:
                    channels[ch] = {"producers": [], "consumers": []}
                channels[ch]["consumers"].append(ep.id)

        for prod in producers:
            ch = prod.channel
            # HTTP calls are separate consumers of the path namespace in this
            # pass — they get their own pass below so message links never
            # collide with REST paths.
            if not ch or ch == "unknown" or prod.type == ProducerType.HTTP_CALL:
                continue
            if ch not in channels:
                channels[ch] = {"producers": [], "consumers": []}
            channels[ch]["producers"].append(prod.id)

        # Build links — only include channels with both producers and consumers.
        # A repo that both publishes and consumes the same channel is
        # self-addressing on it (a re-queue/re-drive into its own subscription,
        # or a broker-side loop): its own producers never form a cross-repo
        # edge to its own consumers' peers — only EXTERNAL producers do. So
        # A repo that both publishes and consumes a channel is
        # self-addressing (a re-drive into its own subscription, or a
        # broker-side loop). Its producers are dropped — but ONLY when every
        # consuming repo also produces (a closed re-drive loop between peers:
        # two self-consumers must not link to each other). When a PURE
        # consumer exists (Axon/event-sourcing shape: the source projects its
        # own event while other services consume it), the publish is a real
        # broadcast and the source's producer forms the edge — per-pair
        # same-repo edges are already skipped downstream (UI edge builder).
        consumer_repos = {
            ch: {ep.repo for ep in eps}
            for ch, eps in _consumers_by_channel(entry_points).items()
        }
        prod_lookup = {p.id: p.repo for p in producers}
        for channel, data in channels.items():
            if not (data["producers"] and data["consumers"]):
                continue
            self_repos = consumer_repos.get(channel, set())
            prod_repos = {prod_lookup.get(pid) for pid in data["producers"]}
            closed_loop = self_repos <= prod_repos
            external = [
                pid for pid in data["producers"]
                if prod_lookup.get(pid) not in self_repos or not closed_loop
            ]
            if external:
                links.append(CrossRepoLink(
                    channel=channel,
                    producers=external,
                    consumers=data["consumers"],
                ))

        # ── http pass: sync calls → REST endpoints via normalized paths ──
        rest_by_path: dict[str, list[tuple[EntryPoint, str]]] = {}
        for ep in entry_points:
            if ep.type != EntryPointType.REST_ENDPOINT:
                continue
            ch = ep.channel or ""
            if ch and ch != "unknown":
                rest_by_path.setdefault(http_paths.normalize_http_path(ch), []).append((ep, ch))

        http_index: dict[tuple[str, str], dict] = {}  # (norm_path, verb) -> link data
        for prod in producers:
            if prod.type != ProducerType.HTTP_CALL:
                continue
            ch = prod.channel or ""
            if not ch or ch == "unknown":
                continue
            norm = http_paths.normalize_http_path(ch)
            verb = (prod.message_type or "").upper()
            for ep, raw_ch in rest_by_path.get(norm, []):
                if ep.repo == prod.repo:
                    continue  # intra-repo — call tree already shows it
                # verb match when both known; otherwise path-only with a
                # lenient key (entry points don't store verbs yet).
                ep_verb = _entry_verb(ep)
                key = (norm, verb if (verb and ep_verb and verb == ep_verb) else "")
                bucket = http_index.setdefault(key, {
                    "channel": ch, "producers": [], "consumers": [],
                    "verb": verb or ep_verb or "",
                })
                if prod.id not in bucket["producers"]:
                    bucket["producers"].append(prod.id)
                if ep.id not in bucket["consumers"]:
                    bucket["consumers"].append(ep.id)

        for bucket in http_index.values():
            if bucket["producers"] and bucket["consumers"]:
                links.append(CrossRepoLink(
                    channel=bucket["channel"],
                    producers=bucket["producers"],
                    consumers=bucket["consumers"],
                    kind="http",
                    verb=bucket["verb"],
                ))

        # ── grpc pass: *Stub calls → GRPC_SERVICE entries, exact /Svc/method ──
        grpc_by_channel: dict[str, list[EntryPoint]] = {}
        for ep in entry_points:
            if ep.type == EntryPointType.GRPC_SERVICE and ep.channel:
                grpc_by_channel.setdefault(ep.channel, []).append(ep)
        if grpc_by_channel:
            grpc_index: dict[str, dict] = {}
            for prod in producers:
                if prod.type != ProducerType.GRPC_CALL:
                    continue
                ch = prod.channel or ""
                if not ch:
                    continue
                for ep in grpc_by_channel.get(ch, []):
                    if ep.repo == prod.repo:
                        continue  # intra-repo — call tree already shows it
                    bucket = grpc_index.setdefault(ch, {
                        "channel": ch, "producers": [], "consumers": [],
                    })
                    if prod.id not in bucket["producers"]:
                        bucket["producers"].append(prod.id)
                    if ep.id not in bucket["consumers"]:
                        bucket["consumers"].append(ep.id)
            for bucket in grpc_index.values():
                if bucket["producers"] and bucket["consumers"]:
                    links.append(CrossRepoLink(
                        channel=bucket["channel"],
                        producers=bucket["producers"],
                        consumers=bucket["consumers"],
                        kind="grpc",
                        verb="GRPC",
                    ))

        return links

    def find_repos_involved(
        self,
        links: list[CrossRepoLink],
        entry_points: list[EntryPoint],
        producers: list[Producer],
    ) -> list[str]:
        """Return all unique repo names involved in cross-repo links."""
        ids = set()
        for link in links:
            ids.update(link.producers)
            ids.update(link.consumers)

        # Extract repo names from IDs (format: "repo:Class.method")
        repos = set()
        ep_lookup = {ep.id: ep.repo for ep in entry_points}
        prod_lookup = {p.id: p.repo for p in producers}

        for id_str in ids:
            if id_str in ep_lookup:
                repos.add(ep_lookup[id_str])
            elif id_str in prod_lookup:
                repos.add(prod_lookup[id_str])
            else:
                # Fallback: extract from ID
                repo = id_str.split(":")[0] if ":" in id_str else "unknown"
                repos.add(repo)

        return sorted(repos)


def _entry_verb(ep: EntryPoint) -> str:
    """Best-effort HTTP verb from a REST entry point's channel/annotations.

    The graph stores no verb on entry points today, so this defaults to ''
    (unknown) unless the channel itself encodes the verb.
    """
    ch = (ep.channel or "").upper()
    for v in ("GET", "POST", "PUT", "DELETE", "PATCH"):
        if ch.startswith(v):
            return v
    return ""


def _consumers_by_channel(entry_points: list[EntryPoint]) -> dict[str, list[EntryPoint]]:
    """Map message-consumer channel → its entry points (for self-addressing)."""
    out: dict[str, list[EntryPoint]] = {}
    for ep in entry_points:
        if ep.type not in MESSAGE_CONSUMER_TYPES:
            continue
        ch = ep.channel
        if not ch or ch in ("unknown", "unknown-event"):
            continue
        out.setdefault(ch, []).append(ep)
    return out
