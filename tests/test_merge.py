"""Explicit node merge."""

from __future__ import annotations

import pytest

from lib.db import rows
from lib.ingest import ingest_payload
from lib.merge import merge_nodes


PAYLOAD = {
    "doc_id": "doc",
    "nodes": [
        {"id": "redis_cache", "label": "Redis Cache", "type": "artifact"},
        {"id": "redis_caching_layer", "label": "Redis Caching Layer", "type": "artifact"},
        {"id": "latency", "label": "Latency", "type": "property"},
        {"id": "cache_miss", "label": "Cache miss", "type": "event"},
    ],
    "rels": [
        {"src": "cache_miss", "dst": "latency", "type": "causes", "evidence": "e1"},
        {"src": "redis_cache", "dst": "cache_miss", "type": "causes", "evidence": "e2"},
        # this rel attaches to redis_caching_layer instead
        {"src": "redis_caching_layer", "dst": "latency", "type": "prevents", "evidence": "e3"},
    ],
}


def test_merge_moves_outgoing_and_incoming(conn, log_root):
    ingest_payload(conn, PAYLOAD, log_root)
    result = merge_nodes(conn, "redis_caching_layer", "redis_cache")
    assert result["src"] == "redis_caching_layer"
    assert result["dst"] == "redis_cache"
    assert result["outgoing_moved"] == 1

    # src removed
    src_rows = rows(conn.execute("MATCH (n:Node {id: 'redis_caching_layer'}) RETURN n"))
    assert src_rows == []

    # rel redirected: redis_cache --prevents--> latency now exists
    res = rows(
        conn.execute(
            """
            MATCH (a:Node {id: 'redis_cache'})-[r:Rel {type:'prevents'}]->(b:Node {id:'latency'})
            RETURN r.evidences AS evs
            """
        )
    )
    assert res and "e3" in res[0]["evs"]


def test_merge_absorbs_existing_edge_of_same_type(conn, log_root):
    # Set up a case where src and dst both have an edge to the same target
    payload = {
        "doc_id": "doc",
        "nodes": [
            {"id": "a", "label": "a", "type": "concept"},
            {"id": "b", "label": "b", "type": "concept"},
            {"id": "c", "label": "c", "type": "concept"},
        ],
        "rels": [
            {"src": "a", "dst": "c", "type": "causes", "evidence": "from-a"},
            {"src": "b", "dst": "c", "type": "causes", "evidence": "from-b"},
        ],
    }
    ingest_payload(conn, payload, log_root)
    merge_nodes(conn, "a", "b")  # a disappears, b absorbs

    # only one edge b->c remains, with both evidences accumulated
    res = rows(
        conn.execute(
            """
            MATCH (a)-[r:Rel {type:'causes'}]->(b:Node {id:'c'})
            RETURN a.id AS sid, r.evidences AS evs, r.sources AS srcs
            """
        )
    )
    assert len(res) == 1
    assert res[0]["sid"] == "b"
    assert set(res[0]["evs"]) == {"from-a", "from-b"}


def test_merge_drops_self_loop(conn, log_root):
    payload = {
        "doc_id": "doc",
        "nodes": [
            {"id": "a", "label": "a", "type": "concept"},
            {"id": "b", "label": "b", "type": "concept"},
        ],
        "rels": [{"src": "a", "dst": "b", "type": "causes", "evidence": "e"}],
    }
    ingest_payload(conn, payload, log_root)
    merge_nodes(conn, "a", "b")
    # a -> b would become b -> b: drop
    res = rows(conn.execute("MATCH ()-[r:Rel]->() RETURN count(*) AS c"))
    assert res[0]["c"] == 0


def test_merge_requires_distinct(conn, log_root):
    ingest_payload(conn, PAYLOAD, log_root)
    with pytest.raises(ValueError):
        merge_nodes(conn, "redis_cache", "redis_cache")


def test_merge_missing_node_raises(conn, log_root):
    ingest_payload(conn, PAYLOAD, log_root)
    with pytest.raises(ValueError):
        merge_nodes(conn, "ghost", "redis_cache")
