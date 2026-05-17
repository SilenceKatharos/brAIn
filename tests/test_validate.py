"""Payload validation and whitelist enforcement."""

import pytest

from lib.validate import REL_TYPES, REFERENCE_NODE_TYPES, validate_payload


def test_accepts_minimal_payload():
    doc_id, res = validate_payload(
        {
            "doc_id": "doc1",
            "nodes": [{"id": "x", "label": "X", "type": "concept"}],
            "rels": [],
        }
    )
    assert doc_id == "doc1"
    assert len(res.nodes) == 1
    assert res.nodes[0].id == "x"
    assert res.nodes[0].importance == pytest.approx(0.5)


def test_accepts_unknown_node_type_and_logs_extension():
    _, res = validate_payload(
        {
            "doc_id": "d",
            "nodes": [
                {"id": "a", "label": "A", "type": "concept"},
                {"id": "b", "label": "B", "type": "wizard"},
            ],
        }
    )
    # Both nodes are accepted — unknown types do not block ingestion.
    assert len(res.nodes) == 2
    assert res.rejected_nodes == []
    # Unknown type is tracked for human review.
    assert len(res.extension_node_types) == 1
    assert res.extension_node_types[0]["type"] == "wizard"


def test_rejects_rel_type_off_whitelist():
    _, res = validate_payload(
        {
            "doc_id": "d",
            "nodes": [
                {"id": "a", "label": "A", "type": "concept"},
                {"id": "b", "label": "B", "type": "concept"},
            ],
            "rels": [
                {"src": "a", "dst": "b", "type": "causes"},
                {"src": "a", "dst": "b", "type": "summons"},
            ],
        }
    )
    assert len(res.rels) == 1
    assert len(res.rejected_rels) == 1
    assert res.rejected_rels[0]["reason"] == "type_not_in_whitelist"


def test_rewrites_id_when_not_canonical():
    _, res = validate_payload(
        {
            "doc_id": "d",
            "nodes": [{"id": "wrong_id", "label": "Hello World", "type": "concept"}],
        }
    )
    assert res.nodes[0].id == "hello_world"
    assert len(res.rewritten_ids) == 1
    assert res.rewritten_ids[0]["proposed"] == "wrong_id"
    assert res.rewritten_ids[0]["canonical"] == "hello_world"


def test_does_not_rewrite_when_already_canonical():
    _, res = validate_payload(
        {
            "doc_id": "d",
            "nodes": [{"id": "hello_world", "label": "Hello World", "type": "concept"}],
        }
    )
    assert res.nodes[0].id == "hello_world"
    assert res.rewritten_ids == []


def test_rejects_missing_label():
    _, res = validate_payload(
        {"doc_id": "d", "nodes": [{"id": "x", "type": "concept"}]}
    )
    assert res.nodes == []
    assert res.rejected_nodes[0]["reason"] == "missing_label"


def test_rejects_self_loop_rel():
    _, res = validate_payload(
        {
            "doc_id": "d",
            "nodes": [{"id": "a", "label": "A", "type": "concept"}],
            "rels": [{"src": "a", "dst": "a", "type": "causes"}],
        }
    )
    assert res.rels == []
    assert res.rejected_rels[0]["reason"] == "self_loop"


def test_clamps_confidence_and_importance():
    _, res = validate_payload(
        {
            "doc_id": "d",
            "nodes": [
                {"id": "a", "label": "A", "type": "concept", "importance": 5},
                {"id": "b", "label": "B", "type": "concept", "importance": -1},
            ],
            "rels": [{"src": "a", "dst": "b", "type": "causes", "confidence": 12}],
        }
    )
    assert res.nodes[0].importance == 1.0
    assert res.nodes[1].importance == 0.0
    assert res.rels[0].confidence == 1.0


def test_missing_doc_id_raises():
    with pytest.raises(ValueError):
        validate_payload({"nodes": [], "rels": []})


def test_vocabularies_contain_expected_types():
    assert "causes" in REL_TYPES
    assert "prevents" in REL_TYPES
    assert "related_to" in REL_TYPES
    assert "concept" in REFERENCE_NODE_TYPES
    assert "artifact" in REFERENCE_NODE_TYPES
    assert "algorithm" in REFERENCE_NODE_TYPES
