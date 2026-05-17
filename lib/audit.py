"""Graph health audit. Emits structured metrics and warning flags."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import kuzu

from lib.db import rows

RELATED_TO_THRESHOLD = 0.05
NO_DESCRIPTION_THRESHOLD = 0.20
ORPHAN_THRESHOLD = 0.10


@dataclass
class AuditReport:
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.errors:
            return 2
        if self.warnings:
            return 1
        return 0


def run_audit(conn: kuzu.Connection) -> AuditReport:
    report = AuditReport()
    _volumes(conn, report)
    _related_to_ratio(conn, report)
    _description_coverage(conn, report)
    _orphan_nodes(conn, report)
    _single_source_rels(conn, report)
    _top_degree_nodes(conn, report)
    _confidence_by_rel_type(conn, report)
    _doc_contributions(conn, report)
    _array_alignment(conn, report)
    return report


def _volumes(conn, report):
    nodes_by_type = rows(
        conn.execute("MATCH (n:Node) RETURN n.type AS type, count(*) AS c ORDER BY c DESC")
    )
    rels_by_type = rows(
        conn.execute("MATCH ()-[r:Rel]->() RETURN r.type AS type, count(*) AS c ORDER BY c DESC")
    )
    total_nodes = rows(conn.execute("MATCH (n:Node) RETURN count(*) AS c"))[0]["c"]
    total_rels = rows(conn.execute("MATCH ()-[r:Rel]->() RETURN count(*) AS c"))[0]["c"]
    report.metrics["total_nodes"] = total_nodes
    report.metrics["total_rels"] = total_rels
    report.metrics["nodes_by_type"] = nodes_by_type
    report.metrics["rels_by_type"] = rels_by_type


def _related_to_ratio(conn, report):
    total = report.metrics["total_rels"]
    if not total:
        report.metrics["related_to_ratio"] = 0.0
        return
    related = rows(
        conn.execute(
            "MATCH ()-[r:Rel {type: 'related_to'}]->() RETURN count(*) AS c"
        )
    )[0]["c"]
    ratio = related / total
    report.metrics["related_to_count"] = related
    report.metrics["related_to_ratio"] = ratio
    if ratio > RELATED_TO_THRESHOLD:
        report.warnings.append(
            f"related_to ratio {ratio:.1%} exceeds {RELATED_TO_THRESHOLD:.0%} threshold"
        )


def _description_coverage(conn, report):
    total = report.metrics["total_nodes"]
    if not total:
        report.metrics["no_description_ratio"] = 0.0
        return
    no_desc = rows(
        conn.execute("MATCH (n:Node) WHERE n.description = '' RETURN count(*) AS c")
    )[0]["c"]
    ratio = no_desc / total
    report.metrics["no_description_count"] = no_desc
    report.metrics["no_description_ratio"] = ratio
    if ratio > NO_DESCRIPTION_THRESHOLD:
        report.warnings.append(
            f"{ratio:.1%} of nodes have no description (threshold {NO_DESCRIPTION_THRESHOLD:.0%})"
        )


def _orphan_nodes(conn, report):
    total = report.metrics["total_nodes"]
    if not total:
        report.metrics["orphan_count"] = 0
        report.metrics["orphan_sample"] = []
        return
    orphans = rows(
        conn.execute(
            """
            MATCH (n:Node)
            WHERE NOT EXISTS { MATCH (n)-[:Rel]-() }
            RETURN n.id AS id, n.label AS label LIMIT 20
            """
        )
    )
    orphan_count_rows = rows(
        conn.execute(
            """
            MATCH (n:Node)
            WHERE NOT EXISTS { MATCH (n)-[:Rel]-() }
            RETURN count(*) AS c
            """
        )
    )
    orphan_count = orphan_count_rows[0]["c"] if orphan_count_rows else 0
    ratio = orphan_count / total
    report.metrics["orphan_count"] = orphan_count
    report.metrics["orphan_ratio"] = ratio
    report.metrics["orphan_sample"] = orphans
    if ratio > ORPHAN_THRESHOLD:
        report.warnings.append(
            f"{ratio:.1%} of nodes are orphans (threshold {ORPHAN_THRESHOLD:.0%})"
        )


def _single_source_rels(conn, report):
    fragile = rows(
        conn.execute(
            "MATCH ()-[r:Rel]->() WHERE size(r.sources) <= 1 RETURN count(*) AS c"
        )
    )[0]["c"]
    report.metrics["single_source_rels"] = fragile


def _top_degree_nodes(conn, report):
    out_top = rows(
        conn.execute(
            """
            MATCH (n:Node)-[r:Rel]->()
            RETURN n.id AS id, n.label AS label, count(r) AS deg
            ORDER BY deg DESC LIMIT 10
            """
        )
    )
    in_top = rows(
        conn.execute(
            """
            MATCH ()-[r:Rel]->(n:Node)
            RETURN n.id AS id, n.label AS label, count(r) AS deg
            ORDER BY deg DESC LIMIT 10
            """
        )
    )
    report.metrics["top_out_degree"] = out_top
    report.metrics["top_in_degree"] = in_top


def _confidence_by_rel_type(conn, report):
    data = rows(
        conn.execute(
            """
            MATCH ()-[r:Rel]->()
            RETURN r.type AS type, avg(r.confidence) AS avg_conf, count(*) AS c
            ORDER BY c DESC
            """
        )
    )
    report.metrics["confidence_by_rel_type"] = data


def _doc_contributions(conn, report):
    """Count how many nodes and rels each doc_id contributed to."""
    raw_nodes = rows(
        conn.execute(
            """
            MATCH (n:Node)
            UNWIND n.sources AS doc
            RETURN doc AS doc_id, count(*) AS node_count
            ORDER BY node_count DESC
            """
        )
    )
    raw_rels = rows(
        conn.execute(
            """
            MATCH ()-[r:Rel]->()
            UNWIND r.sources AS doc
            RETURN doc AS doc_id, count(*) AS rel_count
            ORDER BY rel_count DESC
            """
        )
    )
    nodes_by_doc = {row["doc_id"]: row["node_count"] for row in raw_nodes}
    rels_by_doc = {row["doc_id"]: row["rel_count"] for row in raw_rels}
    all_docs = set(nodes_by_doc) | set(rels_by_doc)
    report.metrics["contributions_by_doc"] = sorted(
        [
            {
                "doc_id": d,
                "nodes": nodes_by_doc.get(d, 0),
                "rels": rels_by_doc.get(d, 0),
            }
            for d in all_docs
        ],
        key=lambda x: (x["nodes"] + x["rels"]),
        reverse=True,
    )


def _array_alignment(conn, report):
    misaligned = rows(
        conn.execute(
            """
            MATCH ()-[r:Rel]->()
            WHERE size(r.sources) <> size(r.evidences)
            RETURN count(*) AS c
            """
        )
    )[0]["c"]
    report.metrics["misaligned_rels"] = misaligned
    if misaligned > 0:
        report.errors.append(
            f"{misaligned} relation(s) have len(sources) != len(evidences) — integrity bug"
        )
