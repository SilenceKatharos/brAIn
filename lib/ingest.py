"""Document ingestion pipeline.

Given a JSON payload describing nodes and relations extracted from a document,
this module purges previous contributions of the same ``doc_id`` and inserts
the new content. See VISION.md sections 4.4 and 5.1 for design rationale.
"""

from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import kuzu

from lib.db import rows
from lib.logs import log_extension_request, log_potential_duplicate
from lib.slugify import slugify
from lib.validate import (
    LintIssue,
    NodePayload,
    RelPayload,
    ValidationResult,
    validate_payload,
)

DUPLICATE_LOOKUP_LIMIT = 5


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class IngestReport:
    doc_id: str
    nodes_created: int = 0
    nodes_updated: int = 0
    rels_created: int = 0
    rels_updated: int = 0
    rels_purged: int = 0
    rels_purged_deleted: int = 0
    rejected_nodes: list[dict] = field(default_factory=list)
    rejected_rels: list[dict] = field(default_factory=list)
    rewritten_ids: list[dict] = field(default_factory=list)
    potential_duplicates: list[dict] = field(default_factory=list)
    skipped_rels: list[dict] = field(default_factory=list)
    lint_issues: list[LintIssue] = field(default_factory=list)
    project_tag_injected: str | None = None

    @property
    def has_issues(self) -> bool:
        """True if anything went wrong silently — the CLI must surface this."""
        return bool(
            self.rejected_nodes
            or self.rejected_rels
            or self.skipped_rels
            or self.rewritten_ids
            or self.lint_issues
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "nodes_created": self.nodes_created,
            "nodes_updated": self.nodes_updated,
            "rels_created": self.rels_created,
            "rels_updated": self.rels_updated,
            "rels_purged_updated": self.rels_purged,
            "rels_purged_deleted": self.rels_purged_deleted,
            "rejected_nodes": self.rejected_nodes,
            "rejected_rels": self.rejected_rels,
            "rewritten_ids": self.rewritten_ids,
            "potential_duplicates": self.potential_duplicates,
            "skipped_rels": self.skipped_rels,
        }


def ingest_payload(
    conn: kuzu.Connection, payload: dict[str, Any], log_root: Path | str
) -> IngestReport:
    """Run the four-phase ingest: validate, purge, upsert nodes, upsert rels.

    Auto-injects ``project:<name>`` from the doc_id (handled by validate),
    surfaces lint issues and skipped rels in the report so the CLI can
    fail loudly.
    """
    from lib.validate import derive_project_tag

    log_root = Path(log_root)
    doc_id, validation = validate_payload(payload)
    report = IngestReport(doc_id=doc_id)
    report.lint_issues = list(validation.lint_issues)
    report.project_tag_injected = derive_project_tag(doc_id)
    _record_validation_artifacts(report, validation, doc_id, log_root)
    _purge_doc_contributions(conn, doc_id, report)
    _upsert_nodes(conn, validation.nodes, doc_id, report, log_root)
    _upsert_rels(conn, validation.rels, doc_id, report)
    return report


def _record_validation_artifacts(
    report: IngestReport, validation: ValidationResult, doc_id: str, log_root: Path
) -> None:
    report.rejected_nodes = validation.rejected_nodes
    report.rejected_rels = validation.rejected_rels
    report.rewritten_ids = validation.rewritten_ids
    for rj in validation.rejected_nodes:
        log_extension_request(
            log_root,
            kind="node_rejected",
            value=str(rj.get("type") or rj.get("reason")),
            doc_id=doc_id,
            detail=rj,
        )
    for rj in validation.rejected_rels:
        log_extension_request(
            log_root,
            kind="rel_rejected",
            value=str(rj.get("type") or rj.get("reason")),
            doc_id=doc_id,
            detail=rj,
        )
    for ext in validation.extension_node_types:
        log_extension_request(
            log_root,
            kind="node_type_extension",
            value=ext["type"],
            doc_id=doc_id,
            detail=ext,
        )


def _purge_doc_contributions(conn: kuzu.Connection, doc_id: str, report: IngestReport) -> None:
    """Remove ``doc_id`` from every Rel.sources/Node.sources it appears in.

    For relations, also remove the parallel evidence entry. Delete the
    relation if its ``sources`` becomes empty. Nodes themselves are never
    deleted by ingest.
    """
    rel_records = rows(
        conn.execute(
            """
            MATCH (a:Node)-[r:Rel]->(b:Node)
            WHERE list_contains(r.sources, $doc_id)
            RETURN a.id AS src, b.id AS dst, r.type AS rtype,
                   r.sources AS sources, r.evidences AS evidences, r.factors AS factors
            """,
            {"doc_id": doc_id},
        )
    )
    for rec in rel_records:
        sources = list(rec["sources"])
        evidences = list(rec["evidences"])
        factors = list(rec["factors"])
        idx = sources.index(doc_id)
        new_sources = sources[:idx] + sources[idx + 1 :]
        new_evidences = evidences[:idx] + evidences[idx + 1 :]
        new_factors = factors[:idx] + factors[idx + 1 :]
        if not new_sources:
            conn.execute(
                """
                MATCH (a:Node {id: $src})-[r:Rel {type: $rtype}]->(b:Node {id: $dst})
                DELETE r
                """,
                {"src": rec["src"], "dst": rec["dst"], "rtype": rec["rtype"]},
            )
            report.rels_purged_deleted += 1
        else:
            conn.execute(
                """
                MATCH (a:Node {id: $src})-[r:Rel {type: $rtype}]->(b:Node {id: $dst})
                SET r.sources = $srcs, r.evidences = $evs, r.factors = $fcts, r.updated_at = $now
                """,
                {
                    "src": rec["src"],
                    "dst": rec["dst"],
                    "rtype": rec["rtype"],
                    "srcs": new_sources,
                    "evs": new_evidences,
                    "fcts": new_factors,
                    "now": _utc_now(),
                },
            )
            report.rels_purged += 1

    node_records = rows(
        conn.execute(
            """
            MATCH (n:Node) WHERE list_contains(n.sources, $doc_id)
            RETURN n.id AS id, n.sources AS sources
            """,
            {"doc_id": doc_id},
        )
    )
    for rec in node_records:
        new_sources = [s for s in rec["sources"] if s != doc_id]
        conn.execute(
            "MATCH (n:Node {id: $id}) SET n.sources = $srcs, n.updated_at = $now",
            {"id": rec["id"], "srcs": new_sources, "now": _utc_now()},
        )


def _upsert_nodes(
    conn: kuzu.Connection,
    nodes: list[NodePayload],
    doc_id: str,
    report: IngestReport,
    log_root: Path,
) -> None:
    for n in nodes:
        existing = rows(
            conn.execute(
                "MATCH (x:Node {id: $id}) RETURN x.description AS descr, x.importance AS imp, x.sources AS srcs",
                {"id": n.id},
            )
        )
        now = _utc_now()
        if existing:
            current = existing[0]
            # If sources is empty the node was just purged of its only doc —
            # treat as fresh: accept the new description and importance.
            sole_owner = not current["srcs"]
            new_description = n.description if sole_owner else (current["descr"] or n.description)
            new_importance = n.importance if sole_owner else max(float(current["imp"]), float(n.importance))
            new_sources = list(dict.fromkeys([*current["srcs"], doc_id, *n.sources]))
            conn.execute(
                """
                MATCH (x:Node {id: $id})
                SET x.label = $label,
                    x.description = $description,
                    x.importance = $importance,
                    x.sources = $sources,
                    x.updated_at = $now
                """,
                {
                    "id": n.id,
                    "label": n.label,
                    "description": new_description,
                    "importance": new_importance,
                    "sources": new_sources,
                    "now": now,
                },
            )
            report.nodes_updated += 1
        else:
            _log_substring_duplicates(conn, n, doc_id, report, log_root)
            conn.execute(
                """
                CREATE (x:Node {
                    id: $id, label: $label, type: $type,
                    description: $description, importance: $importance,
                    created_at: $now, updated_at: $now, sources: $sources
                })
                """,
                {
                    "id": n.id,
                    "label": n.label,
                    "type": n.type,
                    "description": n.description,
                    "importance": n.importance,
                    "now": now,
                    "sources": list(dict.fromkeys([doc_id, *n.sources])),
                },
            )
            report.nodes_created += 1


def _log_substring_duplicates(
    conn: kuzu.Connection,
    new_node: NodePayload,
    doc_id: str,
    report: IngestReport,
    log_root: Path,
) -> None:
    if not new_node.label:
        return
    needle = new_node.label.lower()
    candidates = rows(
        conn.execute(
            """
            MATCH (x:Node)
            WHERE lower(x.label) CONTAINS $needle OR $needle CONTAINS lower(x.label)
            RETURN x.id AS id, x.label AS label, x.type AS type LIMIT $lim
            """,
            {"needle": needle, "lim": DUPLICATE_LOOKUP_LIMIT},
        )
    )
    candidates = [c for c in candidates if c["id"] != new_node.id]
    if not candidates:
        return
    record = {
        "new_id": new_node.id,
        "new_label": new_node.label,
        "candidates": candidates,
    }
    report.potential_duplicates.append(record)
    log_potential_duplicate(
        log_root, new_node.id, new_node.label, candidates, doc_id=doc_id
    )


def _assemble_rel_arrays(
    doc_id: str,
    r: RelPayload,
    current_srcs: list[str] | None = None,
    current_evs: list[str] | None = None,
    current_fcts: list[str] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Build aligned (sources, evidences, factors) arrays.

    Invariant: ``len(sources) == len(evidences) == len(factors)``.
    Each index i represents one CONTRIBUTING SOURCE. The doc_id ingested
    here contributes (r.evidence, r.factor). Extra entries in r.sources
    (project tags, cross-references) contribute empty evidence/factor —
    they are metadata, not provenance with a sentence behind them.
    """
    new_srcs = list(current_srcs or [])
    new_evs = list(current_evs or [])
    new_fcts = list(current_fcts or [])
    # Add or refresh the contribution from this doc_id.
    if doc_id in new_srcs:
        idx = new_srcs.index(doc_id)
        new_evs[idx] = r.evidence
        new_fcts[idx] = r.factor
    else:
        new_srcs.append(doc_id)
        new_evs.append(r.evidence)
        new_fcts.append(r.factor)
    # Add extra sources (project tags etc.) without evidence/factor.
    for extra in r.sources:
        if extra == doc_id or extra in new_srcs:
            continue
        new_srcs.append(extra)
        new_evs.append("")
        new_fcts.append("")
    return new_srcs, new_evs, new_fcts


def _upsert_rels(
    conn: kuzu.Connection, rels: list[RelPayload], doc_id: str, report: IngestReport
) -> None:
    for r in rels:
        src_id = slugify(r.src) if not _node_exists(conn, r.src) else r.src
        dst_id = slugify(r.dst) if not _node_exists(conn, r.dst) else r.dst
        if not _node_exists(conn, src_id) or not _node_exists(conn, dst_id):
            print(
                f"  X skipped rel (missing endpoint): {r.src} --[{r.type}]--> {r.dst}",
                file=sys.stderr,
            )
            report.skipped_rels.append(
                {"reason": "missing_endpoint", "src": r.src, "dst": r.dst, "type": r.type}
            )
            continue
        existing = rows(
            conn.execute(
                """
                MATCH (a:Node {id: $src})-[e:Rel {type: $rtype}]->(b:Node {id: $dst})
                RETURN e.confidence AS c, e.sources AS srcs, e.evidences AS evs, e.factors AS fcts
                """,
                {"src": src_id, "dst": dst_id, "rtype": r.type},
            )
        )
        now = _utc_now()
        if existing:
            current = existing[0]
            new_conf = max(float(current["c"]), float(r.confidence))
            new_srcs, new_evs, new_fcts = _assemble_rel_arrays(
                doc_id, r,
                current_srcs=list(current["srcs"]),
                current_evs=list(current["evs"]),
                current_fcts=list(current["fcts"]),
            )
            conn.execute(
                """
                MATCH (a:Node {id: $src})-[e:Rel {type: $rtype}]->(b:Node {id: $dst})
                SET e.confidence = $c, e.sources = $srcs, e.evidences = $evs,
                    e.factors = $fcts, e.updated_at = $now
                """,
                {
                    "src": src_id,
                    "dst": dst_id,
                    "rtype": r.type,
                    "c": new_conf,
                    "srcs": new_srcs,
                    "evs": new_evs,
                    "fcts": new_fcts,
                    "now": now,
                },
            )
            report.rels_updated += 1
        else:
            new_srcs, new_evs, new_fcts = _assemble_rel_arrays(doc_id, r)
            conn.execute(
                """
                MATCH (a:Node {id: $src}), (b:Node {id: $dst})
                CREATE (a)-[:Rel {
                    type: $rtype, confidence: $c,
                    evidences: $evs, factors: $fcts, sources: $srcs,
                    created_at: $now, updated_at: $now
                }]->(b)
                """,
                {
                    "src": src_id,
                    "dst": dst_id,
                    "rtype": r.type,
                    "c": r.confidence,
                    "evs": new_evs,
                    "fcts": new_fcts,
                    "srcs": new_srcs,
                    "now": now,
                },
            )
            report.rels_created += 1


def _node_exists(conn: kuzu.Connection, node_id: str) -> bool:
    result = conn.execute(
        "MATCH (n:Node {id: $id}) RETURN 1 AS ok LIMIT 1", {"id": node_id}
    )
    return result.has_next()
