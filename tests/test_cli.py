"""CLI smoke tests via click.testing.CliRunner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from brain import cli


PAYLOAD = {
    "doc_id": "cli_doc",
    "nodes": [
        {"id": "alpha", "label": "Alpha", "type": "concept", "description": "first"},
        {"id": "beta", "label": "Beta", "type": "concept", "description": "second"},
    ],
    "rels": [
        {"src": "alpha", "dst": "beta", "type": "causes", "confidence": 0.9, "evidence": "ev1"},
    ],
}


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def db_path(tmp_path: Path):
    return tmp_path / "db"


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    p = tmp_path / "payload.json"
    p.write_text(json.dumps(PAYLOAD), encoding="utf-8")
    return p


def _invoke(runner, db_path, args):
    return runner.invoke(cli, ["--db", str(db_path), *args])


def test_init(runner, db_path):
    result = _invoke(runner, db_path, ["init"])
    assert result.exit_code == 0
    assert "initialized" in result.output


def test_ingest_and_stats(runner, db_path, sample_file):
    _invoke(runner, db_path, ["init"])
    res = _invoke(runner, db_path, ["ingest", str(sample_file)])
    assert res.exit_code == 0
    assert "cli_doc" in res.output

    stats = _invoke(runner, db_path, ["stats"])
    assert stats.exit_code == 0
    assert "Total: 2 nodes, 1 rels" in stats.output


def test_find(runner, db_path, sample_file):
    _invoke(runner, db_path, ["init"])
    _invoke(runner, db_path, ["ingest", str(sample_file)])
    res = _invoke(runner, db_path, ["find", "alpha"])
    assert "alpha" in res.output
    assert "concept" in res.output


def test_show(runner, db_path, sample_file):
    _invoke(runner, db_path, ["init"])
    _invoke(runner, db_path, ["ingest", str(sample_file)])
    res = _invoke(runner, db_path, ["show", "alpha"])
    assert "Alpha" in res.output
    assert "beta" in res.output


def test_causes_effects(runner, db_path, sample_file):
    _invoke(runner, db_path, ["init"])
    _invoke(runner, db_path, ["ingest", str(sample_file)])
    res = _invoke(runner, db_path, ["effects", "alpha"])
    assert res.exit_code == 0
    assert "beta" in res.output


def test_paths(runner, db_path, sample_file):
    _invoke(runner, db_path, ["init"])
    _invoke(runner, db_path, ["ingest", str(sample_file)])
    res = _invoke(runner, db_path, ["paths", "alpha", "beta"])
    assert res.exit_code == 0
    assert "alpha" in res.output and "beta" in res.output


def test_audit_clean(runner, db_path, sample_file):
    _invoke(runner, db_path, ["init"])
    _invoke(runner, db_path, ["ingest", str(sample_file)])
    res = _invoke(runner, db_path, ["audit"])
    # 2 nodes, both with description and degree -- one orphan possible? both connected
    # exit_code may still be 0
    assert "Volumes" in res.output


def test_export_import_round_trip(runner, db_path, sample_file, tmp_path):
    _invoke(runner, db_path, ["init"])
    _invoke(runner, db_path, ["ingest", str(sample_file)])
    dump = tmp_path / "dump.json"
    res = _invoke(runner, db_path, ["export", str(dump)])
    assert res.exit_code == 0
    assert dump.exists()

    db2 = tmp_path / "db2"
    _invoke(runner, db2, ["init"])
    res2 = _invoke(runner, db2, ["import", str(dump), "--strategy", "force"])
    assert res2.exit_code == 0
    stats = _invoke(runner, db2, ["stats"])
    assert "Total: 2 nodes, 1 rels" in stats.output


def test_merge(runner, db_path, tmp_path):
    payload = {
        "doc_id": "doc",
        "nodes": [
            {"id": "redis_cache", "label": "Redis Cache", "type": "artifact"},
            {"id": "redis_caching_layer", "label": "Redis Caching Layer", "type": "artifact"},
            {"id": "latency", "label": "Latency", "type": "property"},
        ],
        "rels": [
            {"src": "redis_caching_layer", "dst": "latency", "type": "prevents", "evidence": "e"},
        ],
    }
    pfile = tmp_path / "payload.json"
    pfile.write_text(json.dumps(payload), encoding="utf-8")
    _invoke(runner, db_path, ["init"])
    _invoke(runner, db_path, ["ingest", str(pfile)])
    res = _invoke(runner, db_path, ["merge", "redis_caching_layer", "INTO", "redis_cache"])
    assert res.exit_code == 0
    assert "merged" in res.output


def test_merge_syntax_check(runner, db_path):
    _invoke(runner, db_path, ["init"])
    res = _invoke(runner, db_path, ["merge", "a", "BEHIND", "b"])
    assert res.exit_code != 0


def test_query(runner, db_path, sample_file):
    _invoke(runner, db_path, ["init"])
    _invoke(runner, db_path, ["ingest", str(sample_file)])
    res = _invoke(runner, db_path, ["query", "MATCH (n:Node) RETURN count(*) AS c"])
    assert res.exit_code == 0
    assert '"c": 2' in res.output


def test_show_missing_node_returns_error(runner, db_path):
    _invoke(runner, db_path, ["init"])
    res = _invoke(runner, db_path, ["show", "ghost"])
    assert res.exit_code == 1


def test_context_json_output(runner, db_path, sample_file):
    _invoke(runner, db_path, ["init"])
    _invoke(runner, db_path, ["ingest", str(sample_file)])
    res = _invoke(runner, db_path, ["context", "alpha"])
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert payload["topic"] == "alpha"
    assert payload["match_count"] >= 1
    assert any(m["id"] == "alpha" for m in payload["matches"])


def test_context_human_readable(runner, db_path, sample_file):
    _invoke(runner, db_path, ["init"])
    _invoke(runner, db_path, ["ingest", str(sample_file)])
    res = _invoke(runner, db_path, ["context", "alpha", "--no-json"])
    assert res.exit_code == 0
    assert "Alpha" in res.output
    assert "match" in res.output.lower()
