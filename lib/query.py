"""Read-side queries: find, show, causes, effects, paths, stats, run_cypher."""

from __future__ import annotations

from typing import Any

import kuzu

from lib.db import rows

CAUSAL_FORWARD = ["causes", "enables", "precedes"]
CAUSAL_BACKWARD = ["causes", "requires", "enables", "precedes"]


def find_nodes(conn: kuzu.Connection, pattern: str, limit: int = 30) -> list[dict]:
    """Return nodes whose id or label contains ``pattern`` (case-insensitive)."""
    needle = pattern.lower()
    return rows(
        conn.execute(
            """
            MATCH (n:Node)
            WHERE lower(n.id) CONTAINS $needle OR lower(n.label) CONTAINS $needle
            RETURN n.id AS id, n.label AS label, n.type AS type,
                   n.description AS description, n.importance AS importance,
                   n.sources AS sources
            ORDER BY n.importance DESC, n.label ASC LIMIT $lim
            """,
            {"needle": needle, "lim": limit},
        )
    )


def show_node(conn: kuzu.Connection, node_id: str) -> dict | None:
    """Return a node along with its outgoing and incoming edges."""
    base = rows(
        conn.execute(
            """
            MATCH (n:Node {id: $id})
            RETURN n.id AS id, n.label AS label, n.type AS type,
                   n.description AS description, n.importance AS importance,
                   n.sources AS sources, n.created_at AS created_at,
                   n.updated_at AS updated_at
            """,
            {"id": node_id},
        )
    )
    if not base:
        return None
    node = base[0]
    node["outgoing"] = rows(
        conn.execute(
            """
            MATCH (a:Node {id: $id})-[r:Rel]->(b:Node)
            RETURN r.type AS type, b.id AS dst, b.label AS dst_label,
                   r.confidence AS confidence, r.evidences AS evidences, r.factors AS factors,
                   r.sources AS sources
            ORDER BY r.confidence DESC, b.label ASC
            """,
            {"id": node_id},
        )
    )
    node["incoming"] = rows(
        conn.execute(
            """
            MATCH (a:Node)-[r:Rel]->(b:Node {id: $id})
            RETURN r.type AS type, a.id AS src, a.label AS src_label,
                   r.confidence AS confidence, r.evidences AS evidences, r.factors AS factors,
                   r.sources AS sources
            ORDER BY r.confidence DESC, a.label ASC
            """,
            {"id": node_id},
        )
    )
    return node


def walk(
    conn: kuzu.Connection,
    start: str,
    direction: str,
    rel_types: list[str],
    depth: int = 3,
) -> list[list[dict]]:
    """Breadth-first traversal returning one frontier per depth level."""
    assert direction in ("out", "in")
    levels: list[list[dict]] = []
    seen = {start}
    frontier = [start]
    for _ in range(depth):
        if direction == "out":
            cypher = """
                MATCH (a:Node)-[r:Rel]->(b:Node)
                WHERE a.id IN $ids AND r.type IN $types
                RETURN a.id AS src, a.label AS src_label,
                       r.type AS type, r.confidence AS confidence,
                       r.evidences AS evidences, r.sources AS sources,
                       b.id AS dst, b.label AS dst_label
            """
        else:
            cypher = """
                MATCH (a:Node)-[r:Rel]->(b:Node)
                WHERE b.id IN $ids AND r.type IN $types
                RETURN a.id AS src, a.label AS src_label,
                       r.type AS type, r.confidence AS confidence,
                       r.evidences AS evidences, r.sources AS sources,
                       b.id AS dst, b.label AS dst_label
            """
        level_rows = rows(conn.execute(cypher, {"ids": frontier, "types": rel_types}))
        if not level_rows:
            break
        levels.append(level_rows)
        next_ids = []
        for row in level_rows:
            new_id = row["dst"] if direction == "out" else row["src"]
            if new_id not in seen:
                seen.add(new_id)
                next_ids.append(new_id)
        if not next_ids:
            break
        frontier = next_ids
    return levels


def causes_of(conn: kuzu.Connection, node_id: str, depth: int = 3) -> list[list[dict]]:
    """BFS upstream via causes/requires/enables/precedes."""
    return walk(conn, node_id, "in", CAUSAL_BACKWARD, depth)


def effects_of(conn: kuzu.Connection, node_id: str, depth: int = 3) -> list[list[dict]]:
    """BFS downstream via causes/enables/precedes."""
    return walk(conn, node_id, "out", CAUSAL_FORWARD, depth)


def paths(conn: kuzu.Connection, src: str, dst: str, max_hops: int = 4, limit: int = 10) -> list[dict]:
    """Find simple paths (up to ``max_hops``) between two nodes."""
    cypher = f"""
        MATCH p = (a:Node {{id: $src}})-[:Rel*1..{max_hops}]->(b:Node {{id: $dst}})
        RETURN nodes(p) AS ns, rels(p) AS rs LIMIT $lim
    """
    raw = rows(conn.execute(cypher, {"src": src, "dst": dst, "lim": limit}))
    out = []
    for r in raw:
        out.append(
            {
                "nodes": [{"id": n["id"], "label": n["label"], "type": n["type"]} for n in r["ns"]],
                "rels": [
                    {"type": e["type"], "confidence": e["confidence"], "factors": e.get("factors", [])} for e in r["rs"]
                ],
            }
        )
    return out


def stats(conn: kuzu.Connection) -> dict[str, Any]:
    """Return counts grouped by node type and relation type."""
    node_counts = rows(
        conn.execute("MATCH (n:Node) RETURN n.type AS type, count(*) AS c ORDER BY c DESC")
    )
    rel_counts = rows(
        conn.execute("MATCH ()-[r:Rel]->() RETURN r.type AS type, count(*) AS c ORDER BY c DESC")
    )
    total_nodes = rows(conn.execute("MATCH (n:Node) RETURN count(*) AS c"))[0]["c"]
    total_rels = rows(conn.execute("MATCH ()-[r:Rel]->() RETURN count(*) AS c"))[0]["c"]
    return {
        "node_counts": node_counts,
        "rel_counts": rel_counts,
        "total_nodes": total_nodes,
        "total_rels": total_rels,
    }


def run_cypher(conn: kuzu.Connection, cypher: str) -> list[dict]:
    """Execute a raw Cypher query and materialize the result."""
    return rows(conn.execute(cypher))


def context_for_topic(
    conn: kuzu.Connection, topic: str, limit: int = 10, neighbors: int = 5
) -> dict:
    """Return existing graph context around a topic.

    Used by extraction pipelines (see SKILL.md, "Using existing graph context")
    to inject already-known nodes and their 1-hop neighborhood into the LLM
    prompt, so it can reuse existing ids rather than minting duplicates.

    The search runs a substring match on id, label and description (case
    insensitive). Matches are ranked by importance, then label. For each
    match, the top-``neighbors`` outgoing and incoming edges (by confidence)
    are attached.
    """
    needle = topic.lower().strip()
    if not needle:
        return {"topic": topic, "matches": [], "match_count": 0}
    pivots = rows(
        conn.execute(
            """
            MATCH (n:Node)
            WHERE lower(n.id) CONTAINS $needle
               OR lower(n.label) CONTAINS $needle
               OR lower(n.description) CONTAINS $needle
            RETURN n.id AS id, n.label AS label, n.type AS type,
                   n.description AS description, n.importance AS importance
            ORDER BY n.importance DESC, n.label ASC LIMIT $lim
            """,
            {"needle": needle, "lim": limit},
        )
    )
    matches = []
    for pivot in pivots:
        outgoing = rows(
            conn.execute(
                """
                MATCH (a:Node {id: $id})-[r:Rel]->(b:Node)
                RETURN r.type AS rel_type, r.confidence AS confidence,
                       r.evidences AS evidences, r.factors AS factors,
                       b.id AS dst, b.label AS dst_label, b.type AS dst_type
                ORDER BY r.confidence DESC LIMIT $lim
                """,
                {"id": pivot["id"], "lim": neighbors},
            )
        )
        incoming = rows(
            conn.execute(
                """
                MATCH (a:Node)-[r:Rel]->(b:Node {id: $id})
                RETURN r.type AS rel_type, r.confidence AS confidence,
                       r.evidences AS evidences, r.factors AS factors,
                       a.id AS src, a.label AS src_label, a.type AS src_type
                ORDER BY r.confidence DESC LIMIT $lim
                """,
                {"id": pivot["id"], "lim": neighbors},
            )
        )
        matches.append({**pivot, "outgoing": outgoing, "incoming": incoming})
    return {"topic": topic, "matches": matches, "match_count": len(matches)}
