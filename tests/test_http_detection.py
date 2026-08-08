"""Feature 1 regression checks — HTTP sync call detection.

Run with: python tests/run_tests.py
No pytest — stdlib only (repo convention: no extra deps).
"""
from __future__ import annotations
import json, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "repos"

def run_engine(repos, out):
    subprocess.run(
        [sys.executable, "-m", "engine.constellation", *repos, "--output", str(out)],
        cwd=REPO, check=True, capture_output=True,
    )
    return json.loads(out.read_text())

def test_http_producer_type_exists():
    from engine.models import ProducerType
    assert "http-call" in {t.value for t in ProducerType}, "HTTP_CALL producer type missing"

def test_http_link_kind_field():
    from engine.models import CrossRepoLink
    l = CrossRepoLink(channel="/x", producers=[], consumers=[])
    assert l.kind == "message", "CrossRepoLink.kind should default to 'message'"
    l2 = CrossRepoLink(channel="/x", producers=[], consumers=[], kind="http")
    assert l2.kind == "http"
    d = l2.to_dict()
    assert d["kind"] == "http" and "verb" in d

def test_http_client_types_registered():
    from engine.java_index import HTTP_CLIENT_TYPES
    assert "RestTemplate" in HTTP_CLIENT_TYPES and "WebClient" in HTTP_CLIENT_TYPES

def test_java_only_client_set():
    from engine.java_index import HTTP_CLIENT_TYPES
    for t in ("RestClient", "HttpClient", "OkHttpClient", "Client", "WebTarget"):
        assert t in HTTP_CLIENT_TYPES, f"{t} missing from HTTP_CLIENT_TYPES"
    assert "FeignClient" not in HTTP_CLIENT_TYPES  # Feign handled via annotation, not field type

def test_normalize_http_path():
    from engine.entry_detector import EntryPointDetector
    n = EntryPointDetector._normalize_http_path
    assert n("/api/orders/123") == n("/api/orders/{id}"), "path templates should normalize equal"
    assert n("/api/orders") != n("/api/orders/{id}")
    uuid = "550e8400-e29b-41d4-a716-446655440000"
    assert n(f"/api/orders/{uuid}") == n("/api/orders/{id}"), "uuid segments normalize to placeholder"

def test_http_link_uses_normalized_path():
    from engine.cross_repo import CrossRepoLinker
    from engine.models import EntryPoint, EntryPointType, Producer, ProducerType, CrossRepoLink
    caller = Producer(id="a:A.call", repo="order-service", type=ProducerType.HTTP_CALL,
                      channel="/api/fulfillment/status/123", method="A.call", file="A.java", line=1, message_type="GET")
    ep = EntryPoint(id="b:B.status", repo="fulfillment-service", type=EntryPointType.REST_ENDPOINT,
                    channel="/api/fulfillment/status/{id}", class_name="B", method="status",
                    file="B.java", line=1)
    links = CrossRepoLinker().link([ep], [caller])
    http = [l for l in links if l.kind == "http"]
    assert len(http) == 1, f"expected 1 http link, got {links}"
    assert http[0].channel == "/api/fulfillment/status/123"
    assert http[0].verb == "GET"

def test_http_calls_detected_and_linked():
    repos = [str(FIXTURES / r) for r in ("order-service", "fulfillment-service", "notification-service")]
    with tempfile.TemporaryDirectory() as td:
        g = run_engine(repos, Path(td) / "g.json")
    http_prods = [p for p in g["producers"] if p["type"] == "http-call"]
    assert http_prods, "expected at least one http-call producer"
    http_links = [l for l in g["cross_repo_links"] if l.get("kind") == "http"]
    assert http_links, "expected at least one http cross-repo link"
    print(f"  http-call producers={len(http_prods)} http links={len(http_links)}")