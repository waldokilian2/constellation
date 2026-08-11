"""
HTTP path utilities — language-neutral helpers for matching REST paths.

Centralised here so the cross-repo linker (``cross_repo.py``) and the
entry-point detector (``entry_detector.py``) share one canonical form, without
the linker depending on a Java-specific detector module. Kept stdlib-only and
pure.
"""
from __future__ import annotations


def normalize_http_path(path: str) -> str:
    """Canonical form for HTTP path matching.

    Replace ``{id}`` template segments **and** literal-id segments (numeric ids,
    UUIDs) with a placeholder, so ``/api/orders/123`` matches
    ``/api/orders/{id}``. Used by the HTTP-call pass of the cross-repo linker.
    """
    segs = path.strip("/").split("/")
    norm = []
    for s in segs:
        if not s:
            continue
        if "{" in s or "}" in s:
            norm.append("{p}")
        elif s.isdigit() or (len(s) == 36 and "-" in s):  # numeric id / uuid
            norm.append("{p}")
        else:
            norm.append(s)
    return "/" + "/".join(norm)


def strip_http_origin(channel: str) -> str:
    """``'http://host:port/api/x'`` → ``'/api/x'``; ``'/api/x'`` stays as-is."""
    for scheme in ("https://", "http://"):
        if channel.startswith(scheme):
            rest = channel[len(scheme):]
            slash = rest.find("/")
            return rest[slash:] if slash >= 0 else "/"
    return channel
