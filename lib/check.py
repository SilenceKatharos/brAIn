"""Pre-ingest payload check.

Runs all the verifications that should happen BEFORE writing to the graph:
- schema validation (via lib.validate)
- endpoint reachability (every rel.src and rel.dst exists in payload or graph)
- causal balance (must contain at least one causes/prevents/contradicts edge
  unless the payload is very small)
- substring duplicate detection against the existing graph

The CLI ``brain check`` runs this as a dry-run. The CLI ``brain ingest``
runs it automatically and aborts unless ``--force`` is passed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import kuzu

from lib.db import rows
from lib.slugify import slugify
from lib.validate import (
    CAUSAL_REL_TYPES,
    LintIssue,
    ValidationResult,
    validate_payload,
)

CAUSAL_REQUIRED_TYPES = frozenset(["causes", "prevents", "contradicts"])
CAUSAL_CHECK_MIN_RELS = 5  # below this, don't require causal edges


@dataclass
class CheckReport:
    doc_id: str
    project_tag: str | None = None
    lint_issues: list[LintIssue] = field(default_factory=list)
    rewritten_ids: list[dict] = field(default_factory=list)
    rejected_nodes: list[dict] = field(default_factory=list)
    rejected_rels: list[dict] = field(default_factory=list)
    missing_endpoints: list[dict] = field(default_factory=list)
    potential_duplicates: list[dict] = field(default_factory=list)
    causal_check_passed: bool = True
    causal_check_reason: str = ""
    node_count: int = 0
    rel_count: int = 0

    @property
    def has_errors(self) -> bool:
        """An error is something that should block ingest unless --force."""
        return bool(
            self.rejected_nodes
            or self.rejected_rels
            or self.missing_endpoints
            or not self.causal_check_passed
        )

    @property
    def has_warnings(self) -> bool:
        return bool(self.lint_issues or self.rewritten_ids or self.potential_duplicates)


def check_payload(payload: dict[str, Any], conn: kuzu.Connection | None) -> CheckReport:
    """Run all pre-ingest checks. Does not modify the graph.

    ``conn`` is optional. When provided, endpoint reachability also looks
    up the existing graph (so a rel can reference a node from a prior doc).
    """
    from lib.validate import derive_project_tag

    doc_id, validation = validate_payload(payload)
    report = CheckReport(
        doc_id=doc_id,
        project_tag=derive_project_tag(doc_id),
        lint_issues=validation.lint_issues,
        rewritten_ids=validation.rewritten_ids,
        rejected_nodes=validation.rejected_nodes,
        rejected_rels=validation.rejected_rels,
        node_count=len(validation.nodes),
        rel_count=len(validation.rels),
    )
    _check_endpoints(validation, conn, report)
    _check_causal_balance(validation, report)
    if conn is not None:
        _check_substring_duplicates(validation, conn, report)
    return report


def _check_endpoints(validation: ValidationResult, conn: kuzu.Connection | None, report: CheckReport) -> None:
    payload_ids = {n.id for n in validation.nodes}
    for r in validation.rels:
        src_id = slugify(r.src)
        dst_id = slugify(r.dst)
        src_ok = src_id in payload_ids or (conn is not None and _node_exists(conn, src_id))
        dst_ok = dst_id in payload_ids or (conn is not None and _node_exists(conn, dst_id))
        if not src_ok or not dst_ok:
            report.missing_endpoints.append({
                "src": r.src,
                "dst": r.dst,
                "type": r.type,
                "src_missing": not src_ok,
                "dst_missing": not dst_ok,
            })


def _check_causal_balance(validation: ValidationResult, report: CheckReport) -> None:
    n = len(validation.rels)
    if n < CAUSAL_CHECK_MIN_RELS:
        report.causal_check_passed = True
        report.causal_check_reason = f"payload has only {n} rels, causal check skipped"
        return
    causal_count = sum(1 for r in validation.rels if r.type in CAUSAL_REQUIRED_TYPES)
    if causal_count == 0:
        report.causal_check_passed = False
        report.causal_check_reason = (
            f"0 causes/prevents/contradicts edges out of {n} rels — payload is purely structural; "
            f"every real project has at least one cause-effect or tradeoff"
        )
    else:
        report.causal_check_passed = True


def _check_substring_duplicates(validation: ValidationResult, conn: kuzu.Connection, report: CheckReport) -> None:
    for n in validation.nodes:
        needle = n.label.lower()
        candidates = rows(
            conn.execute(
                """
                MATCH (x:Node)
                WHERE lower(x.label) CONTAINS $needle OR $needle CONTAINS lower(x.label)
                RETURN x.id AS id, x.label AS label, x.type AS type LIMIT 3
                """,
                {"needle": needle},
            )
        )
        candidates = [c for c in candidates if c["id"] != n.id]
        if candidates:
            report.potential_duplicates.append({
                "new_id": n.id,
                "new_label": n.label,
                "candidates": candidates,
            })


def _node_exists(conn: kuzu.Connection, node_id: str) -> bool:
    result = conn.execute(
        "MATCH (n:Node {id: $id}) RETURN 1 AS ok LIMIT 1", {"id": node_id}
    )
    return result.has_next()
