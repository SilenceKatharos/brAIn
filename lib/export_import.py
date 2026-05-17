"""Graph dump/restore as a single JSON document.

Format::

    {
      "version": "0.1",
      "exported_at": "...Z",
      "nodes": [{...all node props...}, ...],
      "rels":  [{src, dst, type, confidence, evidences, sources,
                 created_at, updated_at}, ...]
    }
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import kuzu

from lib.db import rows, init_schema

EXPORT_VERSION = "0.1"


def export_graph(conn: kuzu.Connection) -> dict[str, Any]:
    """Return the full graph as a serialisable dict."""
    node_rows = rows(
        conn.execute(
            """
            MATCH (n:Node)
            RETURN n.id AS id, n.label AS label, n.type AS type,
                   n.description AS description, n.importance AS importance,
                   n.created_at AS created_at, n.updated_at AS updated_at,
                   n.sources AS sources
            ORDER BY n.id
            """
        )
    )
    rel_rows = rows(
        conn.execute(
            """
            MATCH (a:Node)-[r:Rel]->(b:Node)
            RETURN a.id AS src, b.id AS dst, r.type AS type,
                   r.confidence AS confidence,
                   r.evidences AS evidences, r.sources AS sources,
                   r.created_at AS created_at, r.updated_at AS updated_at
            ORDER BY a.id, b.id, r.type
            """
        )
    )
    return {
        "version": EXPORT_VERSION,
        "exported_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "nodes": node_rows,
        "rels": rel_rows,
    }


def write_export(conn: kuzu.Connection, path: Path | str) -> Path:
    path = Path(path)
    payload = export_graph(conn)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def import_graph(
    conn: kuzu.Connection, payload: dict[str, Any], strategy: str = "force"
) -> dict[str, int]:
    """Load a dump produced by :func:`export_graph`.

    Strategies:
      * ``force`` — wipe existing data first (default).
      * ``merge`` — keep existing data; cumulate sources/evidences on conflicts.
    """
    if strategy not in ("force", "merge"):
        raise ValueError(f"unknown strategy: {strategy}")
    init_schema(conn)
    if strategy == "force":
        conn.execute("MATCH ()-[r:Rel]->() DELETE r")
        conn.execute("MATCH (n:Node) DETACH DELETE n")
    nodes_in = payload.get("nodes", []) or []
    rels_in = payload.get("rels", []) or []
    n_created = n_updated = 0
    r_created = r_updated = 0
    for n in nodes_in:
        if _node_exists(conn, n["id"]):
            if strategy == "force":
                # Should not happen post-wipe but be defensive.
                continue
            current = rows(
                conn.execute(
                    "MATCH (x:Node {id: $id}) RETURN x.sources AS srcs, x.importance AS imp, x.description AS descr",
                    {"id": n["id"]},
                )
            )[0]
            new_sources = list({*current["srcs"], *(n.get("sources") or [])})
            new_imp = max(float(current["imp"]), float(n.get("importance", 0.5)))
            new_desc = current["descr"] or n.get("description", "")
            conn.execute(
                """
                MATCH (x:Node {id: $id})
                SET x.label = $label, x.description = $description,
                    x.importance = $importance, x.sources = $sources,
                    x.updated_at = $updated_at
                """,
                {
                    "id": n["id"],
                    "label": n["label"],
                    "description": new_desc,
                    "importance": new_imp,
                    "sources": new_sources,
                    "updated_at": n.get("updated_at", ""),
                },
            )
            n_updated += 1
        else:
            conn.execute(
                """
                CREATE (x:Node {
                    id: $id, label: $label, type: $type,
                    description: $description, importance: $importance,
                    created_at: $created_at, updated_at: $updated_at,
                    sources: $sources
                })
                """,
                {
                    "id": n["id"],
                    "label": n["label"],
                    "type": n["type"],
                    "description": n.get("description", ""),
                    "importance": float(n.get("importance", 0.5)),
                    "created_at": n.get("created_at", ""),
                    "updated_at": n.get("updated_at", ""),
                    "sources": n.get("sources", []) or [],
                },
            )
            n_created += 1
    for r in rels_in:
        if not (_node_exists(conn, r["src"]) and _node_exists(conn, r["dst"])):
            continue
        existing = rows(
            conn.execute(
                """
                MATCH (a:Node {id: $src})-[e:Rel {type: $rtype}]->(b:Node {id: $dst})
                RETURN e.confidence AS c, e.sources AS srcs, e.evidences AS evs
                """,
                {"src": r["src"], "dst": r["dst"], "rtype": r["type"]},
            )
        )
        if existing:
            cur = existing[0]
            new_conf = max(float(cur["c"]), float(r.get("confidence", 0.8)))
            new_sources = list(cur["srcs"]) + list(r.get("sources", []) or [])
            new_evs = list(cur["evs"]) + list(r.get("evidences", []) or [])
            conn.execute(
                """
                MATCH (a:Node {id: $src})-[e:Rel {type: $rtype}]->(b:Node {id: $dst})
                SET e.confidence = $c, e.sources = $srcs, e.evidences = $evs,
                    e.updated_at = $updated_at
                """,
                {
                    "src": r["src"],
                    "dst": r["dst"],
                    "rtype": r["type"],
                    "c": new_conf,
                    "srcs": new_sources,
                    "evs": new_evs,
                    "updated_at": r.get("updated_at", ""),
                },
            )
            r_updated += 1
        else:
            conn.execute(
                """
                MATCH (a:Node {id: $src}), (b:Node {id: $dst})
                CREATE (a)-[:Rel {
                    type: $rtype, confidence: $c,
                    evidences: $evs, sources: $srcs,
                    created_at: $created_at, updated_at: $updated_at
                }]->(b)
                """,
                {
                    "src": r["src"],
                    "dst": r["dst"],
                    "rtype": r["type"],
                    "c": float(r.get("confidence", 0.8)),
                    "evs": r.get("evidences", []) or [],
                    "srcs": r.get("sources", []) or [],
                    "created_at": r.get("created_at", ""),
                    "updated_at": r.get("updated_at", ""),
                },
            )
            r_created += 1
    return {
        "nodes_created": n_created,
        "nodes_updated": n_updated,
        "rels_created": r_created,
        "rels_updated": r_updated,
    }


def read_import(conn: kuzu.Connection, path: Path | str, strategy: str = "force") -> dict[str, int]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return import_graph(conn, payload, strategy=strategy)


def _node_exists(conn: kuzu.Connection, node_id: str) -> bool:
    result = conn.execute("MATCH (n:Node {id: $id}) RETURN 1 LIMIT 1", {"id": node_id})
    return result.has_next()
