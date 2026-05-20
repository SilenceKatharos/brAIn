"""Payload validation.

Node types are open: any non-empty string is accepted. Unknown types are
tracked in ValidationResult.extension_node_types for logging but do not
block ingestion. Coherence comes from the LLM querying the graph before
creating nodes (query_graph tool), not from a hard type constraint.

Relation types are strict: only whitelisted values are accepted. The
causal vocabulary must not drift — it is the semantic core of the graph.

Lint issues (label slug pitfalls, description length, evidence length)
are surfaced via ValidationResult.lint_issues. They never block ingest
on their own but the CLI exits non-zero unless --force is passed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from lib.slugify import slugify, SlugifyError

DESCRIPTION_MIN_LEN = 30
DESCRIPTION_MAX_LEN = 400
EVIDENCE_MIN_LEN = 30
LABEL_FORBIDDEN_CHARS = re.compile(r"[()/.+]")

REFERENCE_NODE_TYPES = frozenset(
    [
        "concept",
        "entity",
        "event",
        "claim",
        "mechanism",
        "algorithm",
        "property",
        "person",
        "place",
        "artifact",
        "process",
    ]
)

CAUSAL_REL_TYPES = frozenset(
    ["causes", "prevents", "requires", "enables", "precedes", "contradicts"]
)
STRUCTURAL_REL_TYPES = frozenset(
    ["is_a", "part_of", "instance_of", "similar_to", "property_of"]
)
FALLBACK_REL_TYPES = frozenset(["related_to"])
REL_TYPES = CAUSAL_REL_TYPES | STRUCTURAL_REL_TYPES | FALLBACK_REL_TYPES


@dataclass
class NodePayload:
    """Normalized node ready for insertion."""

    id: str
    label: str
    type: str
    description: str = ""
    importance: float = 0.5
    sources: list[str] = field(default_factory=list)


@dataclass
class RelPayload:
    """Normalized relation ready for insertion."""

    src: str
    dst: str
    type: str
    confidence: float = 0.8
    evidence: str = ""
    factor: str = ""
    sources: list[str] = field(default_factory=list)


@dataclass
class LintIssue:
    """Non-blocking quality warning surfaced at ingest time."""
    kind: str  # 'label_forbidden_char' | 'description_too_long' | 'description_too_short' | 'evidence_too_short'
    target: str  # node id or rel "src->dst:type"
    detail: str


@dataclass
class ValidationResult:
    nodes: list[NodePayload] = field(default_factory=list)
    rels: list[RelPayload] = field(default_factory=list)
    rejected_nodes: list[dict] = field(default_factory=list)
    rejected_rels: list[dict] = field(default_factory=list)
    rewritten_ids: list[dict] = field(default_factory=list)
    extension_node_types: list[dict] = field(default_factory=list)  # unknown types, accepted but logged
    lint_issues: list[LintIssue] = field(default_factory=list)


_DOC_ID_PATTERN = re.compile(r"^project_([a-z0-9]+)(?:_.+)?$")


def derive_project_tag(doc_id: str) -> str | None:
    """Return 'project:<name>' for doc_ids following 'project_<name>_<aspect>'.

    Returns None if the doc_id doesn't match the convention, so callers can
    skip the auto-tag injection (e.g. for non-project docs).
    """
    m = _DOC_ID_PATTERN.match(doc_id)
    return f"project:{m.group(1)}" if m else None


def validate_payload(payload: dict[str, Any]) -> tuple[str, ValidationResult]:
    """Validate and normalize a raw ingest payload.

    Auto-injects ``project:<name>`` into every node and rel ``sources`` when
    the doc_id follows the project convention. Surfaces lint issues
    (label slugs, description length, evidence length) via
    ``ValidationResult.lint_issues``. Caller is responsible for logging
    the rejection/rewrite lists.
    """
    doc_id = payload.get("doc_id")
    if not doc_id or not isinstance(doc_id, str):
        raise ValueError("payload requires a non-empty string 'doc_id'")

    res = ValidationResult()

    for raw in payload.get("nodes", []) or []:
        normalized = _validate_node(raw, res)
        if normalized is not None:
            res.nodes.append(normalized)

    for raw in payload.get("rels", []) or []:
        normalized = _validate_rel(raw, res)
        if normalized is not None:
            res.rels.append(normalized)

    project_tag = derive_project_tag(doc_id)
    if project_tag:
        for n in res.nodes:
            if project_tag not in n.sources:
                n.sources.append(project_tag)
        for r in res.rels:
            if project_tag not in r.sources:
                r.sources.append(project_tag)

    return doc_id, res


def _validate_node(raw: dict, res: ValidationResult) -> NodePayload | None:
    if not isinstance(raw, dict):
        res.rejected_nodes.append({"reason": "not_a_dict", "raw": raw})
        return None
    label = raw.get("label")
    ntype = raw.get("type")
    if not label or not isinstance(label, str):
        res.rejected_nodes.append({"reason": "missing_label", "raw": raw})
        return None
    if not ntype or not isinstance(ntype, str):
        res.rejected_nodes.append({"reason": "missing_type", "raw": raw})
        return None
    if ntype not in REFERENCE_NODE_TYPES:
        res.extension_node_types.append({"type": ntype, "label": label})
    if LABEL_FORBIDDEN_CHARS.search(label):
        res.lint_issues.append(LintIssue(
            kind="label_forbidden_char",
            target=label,
            detail=f"label contains one of ()/.+ — slug will silently rewrite the id; rename label to plain ASCII to avoid",
        ))
    try:
        canonical = slugify(label)
    except SlugifyError as exc:
        res.rejected_nodes.append({"reason": "label_unslugifiable", "raw": raw, "error": str(exc)})
        return None
    proposed = raw.get("id")
    if proposed and proposed != canonical:
        res.rewritten_ids.append({"proposed": proposed, "canonical": canonical, "label": label})
    description = (raw.get("description") or "").strip()
    if description:
        if len(description) > DESCRIPTION_MAX_LEN:
            res.lint_issues.append(LintIssue(
                kind="description_too_long",
                target=canonical,
                detail=f"description is {len(description)} chars (> {DESCRIPTION_MAX_LEN}) — paragraph antipattern; one disambiguating sentence is enough",
            ))
        elif len(description) < DESCRIPTION_MIN_LEN:
            res.lint_issues.append(LintIssue(
                kind="description_too_short",
                target=canonical,
                detail=f"description is {len(description)} chars (< {DESCRIPTION_MIN_LEN}) — too poor to make the source disposable",
            ))
    importance = raw.get("importance", 0.5)
    try:
        importance = float(importance)
    except (TypeError, ValueError):
        importance = 0.5
    importance = max(0.0, min(1.0, importance))
    raw_sources = raw.get("sources") or []
    extra_sources = [s for s in raw_sources if isinstance(s, str)]
    return NodePayload(
        id=canonical,
        label=label.strip(),
        type=ntype,
        description=(raw.get("description") or "").strip(),
        importance=importance,
        sources=extra_sources,
    )


def _validate_rel(raw: dict, res: ValidationResult) -> RelPayload | None:
    if not isinstance(raw, dict):
        res.rejected_rels.append({"reason": "not_a_dict", "raw": raw})
        return None
    src = raw.get("src")
    dst = raw.get("dst")
    rtype = raw.get("type")
    if not src or not dst or not isinstance(src, str) or not isinstance(dst, str):
        res.rejected_rels.append({"reason": "missing_endpoints", "raw": raw})
        return None
    if rtype not in REL_TYPES:
        res.rejected_rels.append({"reason": "type_not_in_whitelist", "type": rtype, "raw": raw})
        return None
    if src == dst:
        res.rejected_rels.append({"reason": "self_loop", "raw": raw})
        return None
    confidence = raw.get("confidence", 0.8)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.8
    confidence = max(0.0, min(1.0, confidence))
    raw_sources = raw.get("sources") or []
    extra_sources = [s for s in raw_sources if isinstance(s, str)]
    evidence = (raw.get("evidence") or "").strip()
    if evidence and len(evidence) < EVIDENCE_MIN_LEN:
        res.lint_issues.append(LintIssue(
            kind="evidence_too_short",
            target=f"{src}->{dst}:{rtype}",
            detail=f"evidence is {len(evidence)} chars (< {EVIDENCE_MIN_LEN}) — must explain 'X causes Y because Z', not a single word",
        ))
    return RelPayload(
        src=src,
        dst=dst,
        type=rtype,
        confidence=confidence,
        evidence=evidence,
        factor=(raw.get("factor") or "").strip(),
        sources=extra_sources,
    )
