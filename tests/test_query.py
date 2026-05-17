"""find, show, causes, effects, paths, stats."""

from __future__ import annotations

import pytest

from lib import query as query_mod
from lib.ingest import ingest_payload


CHAIN_PAYLOAD = {
    "doc_id": "chain",
    "nodes": [
        {"id": "ttl_too_short", "label": "TTL too short", "type": "claim"},
        {"id": "cache_miss", "label": "Cache miss", "type": "event"},
        {"id": "high_latency", "label": "High latency", "type": "event"},
        {"id": "user_drop_off", "label": "User drop-off", "type": "event"},
        {"id": "unrelated", "label": "Unrelated", "type": "concept"},
    ],
    "rels": [
        {"src": "ttl_too_short", "dst": "cache_miss", "type": "causes", "confidence": 0.9, "evidence": "ev1"},
        {"src": "cache_miss", "dst": "high_latency", "type": "causes", "confidence": 0.8, "evidence": "ev2"},
        {"src": "high_latency", "dst": "user_drop_off", "type": "causes", "confidence": 0.7, "evidence": "ev3"},
    ],
}


@pytest.fixture
def graph(conn, log_root):
    ingest_payload(conn, CHAIN_PAYLOAD, log_root)
    return conn


def test_find_by_substring(graph):
    res = query_mod.find_nodes(graph, "cache")
    ids = {r["id"] for r in res}
    assert "cache_miss" in ids


def test_find_case_insensitive(graph):
    res = query_mod.find_nodes(graph, "LATENCY")
    ids = {r["id"] for r in res}
    assert "high_latency" in ids


def test_find_no_match(graph):
    assert query_mod.find_nodes(graph, "xenoplasm") == []


def test_show_existing_node(graph):
    node = query_mod.show_node(graph, "cache_miss")
    assert node is not None
    assert node["label"] == "Cache miss"
    assert len(node["outgoing"]) == 1
    assert node["outgoing"][0]["dst"] == "high_latency"
    assert len(node["incoming"]) == 1
    assert node["incoming"][0]["src"] == "ttl_too_short"


def test_show_missing_node(graph):
    assert query_mod.show_node(graph, "ghost") is None


def test_causes_walks_upstream(graph):
    levels = query_mod.causes_of(graph, "user_drop_off", depth=4)
    # Level 1 should contain high_latency
    found_ids_by_level = [{row["src"] for row in lvl} for lvl in levels]
    assert "high_latency" in found_ids_by_level[0]
    # walks should reach ttl_too_short by level 3
    all_found = set().union(*found_ids_by_level)
    assert "ttl_too_short" in all_found


def test_effects_walks_downstream(graph):
    levels = query_mod.effects_of(graph, "ttl_too_short", depth=4)
    found = set()
    for lvl in levels:
        for row in lvl:
            found.add(row["dst"])
    assert "cache_miss" in found
    assert "high_latency" in found
    assert "user_drop_off" in found


def test_paths_finds_simple_chain(graph):
    found = query_mod.paths(graph, "ttl_too_short", "user_drop_off", max_hops=4)
    assert found
    p = found[0]
    ids = [n["id"] for n in p["nodes"]]
    assert ids == ["ttl_too_short", "cache_miss", "high_latency", "user_drop_off"]


def test_paths_returns_empty_when_disconnected(graph):
    assert query_mod.paths(graph, "ttl_too_short", "unrelated") == []


def test_stats(graph):
    s = query_mod.stats(graph)
    assert s["total_nodes"] == 5
    assert s["total_rels"] == 3
    rel_types = {row["type"] for row in s["rel_counts"]}
    assert "causes" in rel_types


def test_run_cypher_passthrough(graph):
    res = query_mod.run_cypher(graph, "MATCH (n:Node) RETURN count(*) AS c")
    assert res[0]["c"] == 5


def test_context_returns_matches_and_neighbors(graph):
    ctx = query_mod.context_for_topic(graph, "cache miss", limit=5, neighbors=5)
    assert ctx["topic"] == "cache miss"
    assert ctx["match_count"] >= 1
    pivot = next(m for m in ctx["matches"] if m["id"] == "cache_miss")
    # cache_miss is mid-chain: 1 incoming (ttl_too_short), 1 outgoing (high_latency)
    assert any(e["dst"] == "high_latency" for e in pivot["outgoing"])
    assert any(e["src"] == "ttl_too_short" for e in pivot["incoming"])


def test_context_empty_topic(graph):
    assert query_mod.context_for_topic(graph, "   ")["match_count"] == 0


def test_context_no_match(graph):
    res = query_mod.context_for_topic(graph, "xenoplasm")
    assert res["match_count"] == 0
    assert res["matches"] == []


def test_context_matches_on_description(conn, log_root):
    # ingest a node where the topic word only appears in the description
    from lib.ingest import ingest_payload

    ingest_payload(
        conn,
        {
            "doc_id": "d",
            "nodes": [
                {
                    "id": "embedding_layer",
                    "label": "Embedding layer",
                    "type": "artifact",
                    "description": "Trainable lookup table that turns tokens into vectors.",
                }
            ],
            "rels": [],
        },
        log_root,
    )
    res = query_mod.context_for_topic(conn, "tokens")
    assert any(m["id"] == "embedding_layer" for m in res["matches"])
