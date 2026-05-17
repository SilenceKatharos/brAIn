"""Payload validation.

Node types are open: any non-empty string is accepted. Unknown types are
tracked in ValidationResult.extension_node_types for logging but do not
block ingestion. Coherence comes from the LLM querying the graph before
creating nodes (query_graph tool), not from a hard type constraint.

Relation types are strict: only whitelisted values are accepted. The
causal vocabulary must not drift — it is the semantic core of the graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lib.slugify import slugify, SlugifyError

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


@dataclass
class RelPayload:
    """Normalized relation ready for insertion."""

    src: str
    dst: str
    type: str
    confidence: float = 0.8
    evidence: str = ""
    factor: str = ""


@dataclass
class ValidationResult:
    nodes: list[NodePayload] = field(default_factory=list)
    rels: list[RelPayload] = field(default_factory=list)
    rejected_nodes: list[dict] = field(default_factory=list)
    rejected_rels: list[dict] = field(default_factory=list)
    rewritten_ids: list[dict] = field(default_factory=list)
    extension_node_types: list[dict] = field(default_factory=list)  # unknown types, accepted but logged


def validate_payload(payload: dict[str, Any]) -> tuple[str, ValidationResult]:
    """Validate and normalize a raw ingest payload.

    Returns the ``doc_id`` and a :class:`ValidationResult`. Caller is
    responsible for logging the rejection/rewrite lists.
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
    try:
        canonical = slugify(label)
    except SlugifyError as exc:
        res.rejected_nodes.append({"reason": "label_unslugifiable", "raw": raw, "error": str(exc)})
        return None
    proposed = raw.get("id")
    if proposed and proposed != canonical:
        res.rewritten_ids.append({"proposed": proposed, "canonical": canonical, "label": label})
    importance = raw.get("importance", 0.5)
    try:
        importance = float(importance)
    except (TypeError, ValueError):
        importance = 0.5
    importance = max(0.0, min(1.0, importance))
    return NodePayload(
        id=canonical,
        label=label.strip(),
        type=ntype,
        description=(raw.get("description") or "").strip(),
        importance=importance,
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
    return RelPayload(
        src=src,
        dst=dst,
        type=rtype,
        confidence=confidence,
        evidence=(raw.get("evidence") or "").strip(),
        factor=(raw.get("factor") or "").strip(),
    )
