"""Export/import round-trip and merge strategies."""

from __future__ import annotations

import pytest

from lib.db import rows, connect, init_schema
from lib.export_import import export_graph, import_graph, read_import, write_export
from lib.ingest import ingest_payload


PAYLOAD = {
    "doc_id": "src",
    "nodes": [
        {"id": "alpha", "label": "Alpha", "type": "concept", "description": "first"},
        {"id": "beta", "label": "Beta", "type": "concept"},
    ],
    "rels": [
        {"src": "alpha", "dst": "beta", "type": "causes", "confidence": 0.9, "evidence": "ev1"},
    ],
}


def _graph_signature(conn):
    """Stable signature of the graph for comparison."""
    n = sorted(
        rows(
            conn.execute(
                """
                MATCH (n:Node) RETURN n.id AS id, n.label AS label, n.type AS type,
                                      n.description AS d, n.importance AS i,
                                      n.sources AS s
                """
            )
        ),
        key=lambda r: r["id"],
    )
    r = sorted(
        rows(
            conn.execute(
                """
                MATCH (a:Node)-[r:Rel]->(b:Node)
                RETURN a.id AS src, b.id AS dst, r.type AS type,
                       r.confidence AS c, r.evidences AS evs, r.sources AS srcs
                """
            )
        ),
        key=lambda r: (r["src"], r["dst"], r["type"]),
    )
    return n, r


def test_round_trip_preserves_graph(conn, tmp_path, log_root):
    ingest_payload(conn, PAYLOAD, log_root)
    sig_before = _graph_signature(conn)
    dump_path = tmp_path / "dump.json"
    write_export(conn, dump_path)

    # fresh DB
    db2 = connect(tmp_path / "second")
    init_schema(db2)
    read_import(db2, dump_path, strategy="force")
    sig_after = _graph_signature(db2)
    assert sig_before == sig_after


def test_import_force_wipes(conn, log_root):
    ingest_payload(conn, PAYLOAD, log_root)
    other = {
        "version": "0.1",
        "nodes": [
            {
                "id": "gamma",
                "label": "Gamma",
                "type": "concept",
                "description": "",
                "importance": 0.5,
                "created_at": "t",
                "updated_at": "t",
                "sources": ["dx"],
            }
        ],
        "rels": [],
    }
    import_graph(conn, other, strategy="force")
    nodes = rows(conn.execute("MATCH (n:Node) RETURN n.id AS id"))
    ids = {r["id"] for r in nodes}
    assert ids == {"gamma"}


def test_import_merge_cumulates(conn, log_root):
    ingest_payload(conn, PAYLOAD, log_root)
    # construct a dump-like payload that shares the same rel triple
    other = {
        "version": "0.1",
        "nodes": [
            {
                "id": "alpha",
                "label": "Alpha",
                "type": "concept",
                "description": "",
                "importance": 0.5,
                "created_at": "t",
                "updated_at": "t",
                "sources": ["dx"],
            },
            {
                "id": "beta",
                "label": "Beta",
                "type": "concept",
                "description": "",
                "importance": 0.5,
                "created_at": "t",
                "updated_at": "t",
                "sources": ["dx"],
            },
        ],
        "rels": [
            {
                "src": "alpha",
                "dst": "beta",
                "type": "causes",
                "confidence": 0.6,
                "evidences": ["ev_merged"],
                "sources": ["dx"],
                "created_at": "t",
                "updated_at": "t",
            }
        ],
    }
    import_graph(conn, other, strategy="merge")
    rec = rows(
        conn.execute(
            """
            MATCH (a:Node {id:'alpha'})-[r:Rel {type:'causes'}]->(b:Node {id:'beta'})
            RETURN r.confidence AS c, r.sources AS srcs, r.evidences AS evs
            """
        )
    )[0]
    # max confidence kept
    assert rec["c"] == pytest.approx(0.9)
    assert set(rec["srcs"]) == {"src", "dx"}
    assert set(rec["evs"]) == {"ev1", "ev_merged"}


def test_import_unknown_strategy_raises(conn):
    with pytest.raises(ValueError):
        import_graph(conn, {"nodes": [], "rels": []}, strategy="splat")
