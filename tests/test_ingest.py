"""Ingestion pipeline behaviour."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.db import rows
from lib.ingest import ingest_payload


SAMPLE_PAYLOAD = {
    "doc_id": "doc1",
    "nodes": [
        {"id": "alpha", "label": "Alpha", "type": "concept", "description": "first"},
        {"id": "beta", "label": "Beta", "type": "concept", "description": "second"},
    ],
    "rels": [
        {"src": "alpha", "dst": "beta", "type": "causes", "confidence": 0.9, "evidence": "ev1"},
    ],
}


def _count_nodes(conn):
    return rows(conn.execute("MATCH (n:Node) RETURN count(*) AS c"))[0]["c"]


def _count_rels(conn):
    return rows(conn.execute("MATCH ()-[r:Rel]->() RETURN count(*) AS c"))[0]["c"]


def _get_rel(conn, src, dst, rtype):
    res = rows(
        conn.execute(
            """
            MATCH (a:Node {id: $src})-[r:Rel {type: $rtype}]->(b:Node {id: $dst})
            RETURN r.confidence AS confidence, r.sources AS sources, r.evidences AS evidences
            """,
            {"src": src, "dst": dst, "rtype": rtype},
        )
    )
    return res[0] if res else None


def test_basic_ingest(conn, log_root):
    report = ingest_payload(conn, SAMPLE_PAYLOAD, log_root)
    assert report.nodes_created == 2
    assert report.rels_created == 1
    assert _count_nodes(conn) == 2
    assert _count_rels(conn) == 1
    rel = _get_rel(conn, "alpha", "beta", "causes")
    assert rel["confidence"] == pytest.approx(0.9)
    assert rel["sources"] == ["doc1"]
    assert rel["evidences"] == ["ev1"]


def test_dedup_rel_same_pair_same_type_different_doc(conn, log_root):
    ingest_payload(conn, SAMPLE_PAYLOAD, log_root)
    second = {
        "doc_id": "doc2",
        "nodes": [
            {"id": "alpha", "label": "Alpha", "type": "concept"},
            {"id": "beta", "label": "Beta", "type": "concept"},
        ],
        "rels": [
            {"src": "alpha", "dst": "beta", "type": "causes", "confidence": 0.7, "evidence": "ev2"},
        ],
    }
    report = ingest_payload(conn, second, log_root)
    assert report.rels_updated == 1
    assert _count_rels(conn) == 1
    rel = _get_rel(conn, "alpha", "beta", "causes")
    # max confidence kept
    assert rel["confidence"] == pytest.approx(0.9)
    assert rel["sources"] == ["doc1", "doc2"]
    assert rel["evidences"] == ["ev1", "ev2"]


def test_reingest_same_doc_is_idempotent(conn, log_root):
    ingest_payload(conn, SAMPLE_PAYLOAD, log_root)
    rel_before = _get_rel(conn, "alpha", "beta", "causes")
    ingest_payload(conn, SAMPLE_PAYLOAD, log_root)
    rel_after = _get_rel(conn, "alpha", "beta", "causes")
    assert _count_nodes(conn) == 2
    assert _count_rels(conn) == 1
    assert rel_after["sources"] == rel_before["sources"] == ["doc1"]
    assert rel_after["evidences"] == rel_before["evidences"] == ["ev1"]


def test_reingest_with_modified_doc(conn, log_root):
    ingest_payload(conn, SAMPLE_PAYLOAD, log_root)
    updated = {
        "doc_id": "doc1",
        "nodes": [
            {"id": "alpha", "label": "Alpha", "type": "concept"},
            {"id": "beta", "label": "Beta", "type": "concept"},
        ],
        "rels": [
            {"src": "alpha", "dst": "beta", "type": "causes", "confidence": 0.5, "evidence": "new-ev"},
        ],
    }
    ingest_payload(conn, updated, log_root)
    rel = _get_rel(conn, "alpha", "beta", "causes")
    assert rel["sources"] == ["doc1"]
    assert rel["evidences"] == ["new-ev"]


def test_reingest_keeps_other_doc_contributions(conn, log_root):
    ingest_payload(conn, SAMPLE_PAYLOAD, log_root)
    second_doc = {
        "doc_id": "doc2",
        "nodes": [
            {"id": "alpha", "label": "Alpha", "type": "concept"},
            {"id": "beta", "label": "Beta", "type": "concept"},
        ],
        "rels": [
            {"src": "alpha", "dst": "beta", "type": "causes", "confidence": 0.7, "evidence": "ev2"},
        ],
    }
    ingest_payload(conn, second_doc, log_root)
    # now re-ingest doc1 with no rels
    update_doc1 = {
        "doc_id": "doc1",
        "nodes": [{"id": "alpha", "label": "Alpha", "type": "concept"}],
        "rels": [],
    }
    ingest_payload(conn, update_doc1, log_root)
    rel = _get_rel(conn, "alpha", "beta", "causes")
    assert rel is not None  # still exists thanks to doc2 contribution
    assert rel["sources"] == ["doc2"]
    assert rel["evidences"] == ["ev2"]


def test_reingest_deletes_rel_when_last_source(conn, log_root):
    ingest_payload(conn, SAMPLE_PAYLOAD, log_root)
    assert _count_rels(conn) == 1
    cleared = {
        "doc_id": "doc1",
        "nodes": [
            {"id": "alpha", "label": "Alpha", "type": "concept"},
            {"id": "beta", "label": "Beta", "type": "concept"},
        ],
        "rels": [],
    }
    ingest_payload(conn, cleared, log_root)
    assert _count_rels(conn) == 0
    assert _count_nodes(conn) == 2  # nodes never deleted


def test_node_label_rewrite_to_canonical_id(conn, log_root):
    payload = {
        "doc_id": "d",
        "nodes": [{"id": "WrongId", "label": "Hello World", "type": "concept"}],
        "rels": [],
    }
    report = ingest_payload(conn, payload, log_root)
    assert report.nodes_created == 1
    assert _count_nodes(conn) == 1
    rec = rows(conn.execute("MATCH (n:Node) RETURN n.id AS id"))
    assert rec[0]["id"] == "hello_world"
    assert len(report.rewritten_ids) == 1


def test_invalid_type_logged_not_crashed(conn, log_root):
    payload = {
        "doc_id": "d",
        "nodes": [
            {"id": "alpha", "label": "Alpha", "type": "concept"},
            {"id": "b", "label": "B", "type": "wizard"},
        ],
        "rels": [{"src": "alpha", "dst": "b", "type": "summons"}],
    }
    report = ingest_payload(conn, payload, log_root)
    # Unknown node type is now accepted — both nodes are created.
    assert report.nodes_created == 2
    assert report.rels_created == 0
    assert report.rejected_nodes == []
    assert len(report.rejected_rels) == 1
    log_file = Path(log_root) / "extension_requests.jsonl"
    assert log_file.exists()
    lines = log_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    parsed = [json.loads(l) for l in lines]
    kinds = {p["kind"] for p in parsed}
    # node_type_extension logged for unknown node type; rel_rejected for unknown rel type.
    assert {"node_type_extension", "rel_rejected"} == kinds


def test_skipped_rel_when_endpoint_missing(conn, log_root):
    payload = {
        "doc_id": "d",
        "nodes": [{"id": "alpha", "label": "Alpha", "type": "concept"}],
        "rels": [{"src": "a", "dst": "ghost", "type": "causes"}],
    }
    report = ingest_payload(conn, payload, log_root)
    assert report.rels_created == 0
    assert len(report.skipped_rels) == 1


def test_potential_duplicate_logged(conn, log_root):
    payload1 = {
        "doc_id": "d1",
        "nodes": [
            {"id": "cache_redis", "label": "Cache Redis", "type": "artifact"},
        ],
        "rels": [],
    }
    payload2 = {
        "doc_id": "d2",
        "nodes": [
            {"id": "cache_redis_v2", "label": "Cache Redis v2", "type": "artifact"},
        ],
        "rels": [],
    }
    ingest_payload(conn, payload1, log_root)
    report = ingest_payload(conn, payload2, log_root)
    assert len(report.potential_duplicates) == 1
    log_file = Path(log_root) / "potential_duplicates.jsonl"
    assert log_file.exists()


def test_node_sources_merged_on_reuse(conn, log_root):
    ingest_payload(conn, SAMPLE_PAYLOAD, log_root)
    # second doc references the same node
    payload2 = {
        "doc_id": "doc2",
        "nodes": [{"id": "alpha", "label": "Alpha", "type": "concept"}],
        "rels": [],
    }
    ingest_payload(conn, payload2, log_root)
    rec = rows(conn.execute("MATCH (n:Node {id: 'alpha'}) RETURN n.sources AS srcs"))
    assert set(rec[0]["srcs"]) == {"doc1", "doc2"}


def test_max_importance_kept_on_node_update(conn, log_root):
    ingest_payload(
        conn,
        {
            "doc_id": "d1",
            "nodes": [{"id": "alpha", "label": "Alpha", "type": "concept", "importance": 0.3}],
            "rels": [],
        },
        log_root,
    )
    ingest_payload(
        conn,
        {
            "doc_id": "d2",
            "nodes": [{"id": "alpha", "label": "Alpha", "type": "concept", "importance": 0.8}],
            "rels": [],
        },
        log_root,
    )
    rec = rows(conn.execute("MATCH (n:Node {id: 'alpha'}) RETURN n.importance AS imp"))
    assert rec[0]["imp"] == pytest.approx(0.8)
