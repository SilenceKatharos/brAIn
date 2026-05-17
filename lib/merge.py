"""Explicit node merge: ``brain.py merge SRC INTO DST``.

SRC disappears. DST inherits SRC's incoming and outgoing edges, its sources,
and the max of its importance.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import kuzu

from lib.db import rows


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def merge_nodes(conn: kuzu.Connection, src_id: str, dst_id: str) -> dict[str, Any]:
    """Merge ``src_id`` into ``dst_id``. Returns a small report dict."""
    if src_id == dst_id:
        raise ValueError("src and dst must be different")
    src = _read_node(conn, src_id)
    dst = _read_node(conn, dst_id)
    if src is None:
        raise ValueError(f"src node not found: {src_id}")
    if dst is None:
        raise ValueError(f"dst node not found: {dst_id}")

    moved_out = _redirect_outgoing(conn, src_id, dst_id)
    moved_in = _redirect_incoming(conn, src_id, dst_id)
    _merge_node_metadata(conn, src, dst)
    conn.execute("MATCH (n:Node {id: $id}) DETACH DELETE n", {"id": src_id})
    return {
        "src": src_id,
        "dst": dst_id,
        "outgoing_moved": moved_out,
        "incoming_moved": moved_in,
    }


def _read_node(conn, node_id):
    res = rows(
        conn.execute(
            """
            MATCH (n:Node {id: $id})
            RETURN n.id AS id, n.label AS label, n.type AS type,
                   n.description AS description, n.importance AS importance,
                   n.sources AS sources
            """,
            {"id": node_id},
        )
    )
    return res[0] if res else None


def _redirect_outgoing(conn, src_id, dst_id) -> int:
    """Move SRC -> X edges so they become DST -> X, merging on (type) collisions."""
    out_edges = rows(
        conn.execute(
            """
            MATCH (a:Node {id: $src})-[r:Rel]->(b:Node)
            RETURN b.id AS dst, r.type AS rtype, r.confidence AS confidence,
                   r.evidences AS evidences, r.sources AS sources,
                   r.created_at AS created_at, r.updated_at AS updated_at
            """,
            {"src": src_id},
        )
    )
    moved = 0
    for e in out_edges:
        target = e["dst"]
        if target == dst_id:
            # SRC -> DST: drop, would become a self-loop on DST.
            continue
        _absorb_or_create_rel(
            conn,
            new_src=dst_id,
            new_dst=target,
            rtype=e["rtype"],
            confidence=e["confidence"],
            evidences=e["evidences"],
            sources=e["sources"],
            created_at=e["created_at"],
        )
        moved += 1
    return moved


def _redirect_incoming(conn, src_id, dst_id) -> int:
    in_edges = rows(
        conn.execute(
            """
            MATCH (a:Node)-[r:Rel]->(b:Node {id: $src})
            RETURN a.id AS src, r.type AS rtype, r.confidence AS confidence,
                   r.evidences AS evidences, r.sources AS sources,
                   r.created_at AS created_at, r.updated_at AS updated_at
            """,
            {"src": src_id},
        )
    )
    moved = 0
    for e in in_edges:
        origin = e["src"]
        if origin == dst_id:
            continue
        _absorb_or_create_rel(
            conn,
            new_src=origin,
            new_dst=dst_id,
            rtype=e["rtype"],
            confidence=e["confidence"],
            evidences=e["evidences"],
            sources=e["sources"],
            created_at=e["created_at"],
        )
        moved += 1
    return moved


def _absorb_or_create_rel(
    conn,
    new_src,
    new_dst,
    rtype,
    confidence,
    evidences,
    sources,
    created_at,
):
    existing = rows(
        conn.execute(
            """
            MATCH (a:Node {id: $src})-[e:Rel {type: $rtype}]->(b:Node {id: $dst})
            RETURN e.confidence AS c, e.sources AS srcs, e.evidences AS evs
            """,
            {"src": new_src, "dst": new_dst, "rtype": rtype},
        )
    )
    now = _utc_now()
    if existing:
        cur = existing[0]
        new_conf = max(float(cur["c"]), float(confidence))
        new_sources = list(cur["srcs"]) + list(sources)
        new_evs = list(cur["evs"]) + list(evidences)
        conn.execute(
            """
            MATCH (a:Node {id: $src})-[e:Rel {type: $rtype}]->(b:Node {id: $dst})
            SET e.confidence = $c, e.sources = $srcs, e.evidences = $evs, e.updated_at = $now
            """,
            {
                "src": new_src,
                "dst": new_dst,
                "rtype": rtype,
                "c": new_conf,
                "srcs": new_sources,
                "evs": new_evs,
                "now": now,
            },
        )
    else:
        conn.execute(
            """
            MATCH (a:Node {id: $src}), (b:Node {id: $dst})
            CREATE (a)-[:Rel {
                type: $rtype, confidence: $c,
                evidences: $evs, sources: $srcs,
                created_at: $created_at, updated_at: $now
            }]->(b)
            """,
            {
                "src": new_src,
                "dst": new_dst,
                "rtype": rtype,
                "c": float(confidence),
                "evs": list(evidences),
                "srcs": list(sources),
                "created_at": created_at or now,
                "now": now,
            },
        )


def _merge_node_metadata(conn, src, dst):
    new_sources = list({*src["sources"], *dst["sources"]})
    new_importance = max(float(src["importance"]), float(dst["importance"]))
    new_description = dst["description"] or src["description"]
    conn.execute(
        """
        MATCH (n:Node {id: $id})
        SET n.sources = $sources, n.importance = $importance,
            n.description = $description, n.updated_at = $now
        """,
        {
            "id": dst["id"],
            "sources": new_sources,
            "importance": new_importance,
            "description": new_description,
            "now": _utc_now(),
        },
    )
