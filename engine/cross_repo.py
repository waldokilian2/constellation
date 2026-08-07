"""
Cross-repo linker — finds connections between repos via shared message channels.

Matches producers in one repo to consumers in another by comparing
queue names, topic names, and event type names.
"""
from __future__ import annotations
from .models import EntryPoint, Producer, CrossRepoLink


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
            if ch and ch != "unknown":
                if ch not in channels:
                    channels[ch] = {"producers": [], "consumers": []}
                channels[ch]["producers"].append(prod.id)

        # Build links — only include channels with both producers and consumers
        links: list[CrossRepoLink] = []
        for channel, data in channels.items():
            if data["producers"] and data["consumers"]:
                links.append(CrossRepoLink(
                    channel=channel,
                    producers=data["producers"],
                    consumers=data["consumers"],
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
