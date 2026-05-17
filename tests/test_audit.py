"""Audit metrics and thresholds."""

from __future__ import annotations

from lib import audit as audit_mod
from lib.ingest import ingest_payload


def test_empty_graph_audit(conn):
    report = audit_mod.run_audit(conn)
    assert report.metrics["total_nodes"] == 0
    assert report.metrics["total_rels"] == 0
    assert report.exit_code == 0


def test_audit_volumes(conn, log_root):
    payload = {
        "doc_id": "doc",
        "nodes": [
            {"id": "a", "label": "a", "type": "concept"},
            {"id": "b", "label": "b", "type": "concept"},
        ],
        "rels": [{"src": "a", "dst": "b", "type": "causes", "evidence": "e"}],
    }
    ingest_payload(conn, payload, log_root)
    report = audit_mod.run_audit(conn)
    assert report.metrics["total_nodes"] == 2
    assert report.metrics["total_rels"] == 1


def test_audit_flags_related_to_excess(conn, log_root):
    nodes = [{"id": f"n_{i}", "label": f"n {i}", "type": "concept"} for i in range(10)]
    rels = [
        {"src": "n_0", "dst": "n_1", "type": "causes", "evidence": "e"},
        # Five related_to edges out of six total = 83% > 5%
        {"src": "n_0", "dst": "n_2", "type": "related_to", "evidence": "e"},
        {"src": "n_0", "dst": "n_3", "type": "related_to", "evidence": "e"},
        {"src": "n_0", "dst": "n_4", "type": "related_to", "evidence": "e"},
        {"src": "n_0", "dst": "n_5", "type": "related_to", "evidence": "e"},
        {"src": "n_0", "dst": "n_6", "type": "related_to", "evidence": "e"},
    ]
    ingest_payload(conn, {"doc_id": "doc", "nodes": nodes, "rels": rels}, log_root)
    report = audit_mod.run_audit(conn)
    assert any("related_to" in w for w in report.warnings)
    assert report.exit_code == 1


def test_audit_flags_orphans(conn, log_root):
    payload = {
        "doc_id": "doc",
        "nodes": [
            {"id": "a", "label": "a", "type": "concept"},
            {"id": "b", "label": "b", "type": "concept"},
            {"id": "c", "label": "c", "type": "concept"},
            {"id": "d", "label": "d", "type": "concept"},
            {"id": "e", "label": "e", "type": "concept"},
        ],
        "rels": [{"src": "a", "dst": "b", "type": "causes", "evidence": "e"}],
    }
    ingest_payload(conn, payload, log_root)
    report = audit_mod.run_audit(conn)
    # 3 out of 5 are orphans = 60% > 10%
    assert any("orphan" in w.lower() for w in report.warnings)


def test_audit_descriptions_coverage(conn, log_root):
    nodes = [{"id": f"n_{i}", "label": f"n {i}", "type": "concept"} for i in range(5)]
    rels = [{"src": "n_0", "dst": "n_1", "type": "causes", "evidence": "e"}]
    ingest_payload(conn, {"doc_id": "doc", "nodes": nodes, "rels": rels}, log_root)
    report = audit_mod.run_audit(conn)
    assert report.metrics["no_description_ratio"] == 1.0


def test_audit_contributions_by_doc(conn, log_root):
    payload1 = {
        "doc_id": "d1",
        "nodes": [
            {"id": "a", "label": "a", "type": "concept"},
            {"id": "b", "label": "b", "type": "concept"},
        ],
        "rels": [{"src": "a", "dst": "b", "type": "causes", "evidence": "e"}],
    }
    payload2 = {
        "doc_id": "d2",
        "nodes": [{"id": "c", "label": "c", "type": "concept"}],
        "rels": [],
    }
    ingest_payload(conn, payload1, log_root)
    ingest_payload(conn, payload2, log_root)
    report = audit_mod.run_audit(conn)
    contribs = {row["doc_id"]: row for row in report.metrics["contributions_by_doc"]}
    assert contribs["d1"]["nodes"] == 2
    assert contribs["d1"]["rels"] == 1
    assert contribs["d2"]["nodes"] == 1
