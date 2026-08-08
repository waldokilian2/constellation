"""
Cross-repo linker — finds connections between repos via shared channels.

Two kinds of edges:

* **message** — a producer in one repo sends to a queue/topic/event that a
  consumer in another repo listens on (exact channel-name match).
* **http** (sync calls) — a ``ProducerType.HTTP_CALL`` in one repo calls a
  REST endpoint in another repo. Paths are matched in **normalized template
  form** (``/api/orders/123`` == ``/api/orders/{id}``) and the link records
  the HTTP verb when both sides know it.
"""
from __future__ import annotations
from .entry_detector import EntryPointDetector
from .models import (
    EntryPoint,
    EntryPointType,
    Producer,
    ProducerType,
    CrossRepoLink,
)


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

        # Build links — only include channels with both producers and consumers
        for channel, data in channels.items():
            if data["producers"] and data["consumers"]:
                links.append(CrossRepoLink(
                    channel=channel,
                    producers=data["producers"],
                    consumers=data["consumers"],
                ))

        # ── http pass: sync calls → REST endpoints via normalized paths ──
        rest_by_path: dict[str, list[tuple[EntryPoint, str]]] = {}
        for ep in entry_points:
            if ep.type != EntryPointType.REST_ENDPOINT:
                continue
            ch = ep.channel or ""
            if ch and ch != "unknown":
                rest_by_path.setdefault(EntryPointDetector._normalize_http_path(ch), []).append((ep, ch))

        http_index: dict[tuple[str, str], dict] = {}  # (norm_path, verb) -> link data
        for prod in producers:
            if prod.type != ProducerType.HTTP_CALL:
                continue
            ch = prod.channel or ""
            if not ch or ch == "unknown":
                continue
            norm = EntryPointDetector._normalize_http_path(ch)
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
