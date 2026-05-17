#!/usr/bin/env python3
"""Run agentic ingestion on a set of documents.

Each document is extracted (Claude uses query_graph tool on demand) then
immediately ingested so subsequent documents see the live graph state.

Good pairs to test convergence:
  neural_network + backpropagation   (tightly coupled, many shared concepts)
  transformer + attention             (same architecture cluster)
  gradient_descent + backpropagation (optimization + training loop)

Usage:
  python ingest_agentic.py \\
    --docs corpus/wikipedia/neural_network.md corpus/wikipedia/backpropagation.md \\
    --db graph/kuzu_db \\
    --out-dir experiments/ai_wiki/results/agentic

The script wipes the DB before running so each run starts clean.
Pass --no-wipe to accumulate on an existing graph instead.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import anthropic  # noqa: E402

from lib.audit import run_audit  # noqa: E402
from lib.db import connect, init_schema  # noqa: E402
from lib.ingest import ingest_payload  # noqa: E402
from lib.query import stats as compute_stats  # noqa: E402
import extract as extract_mod  # noqa: E402

DEFAULT_DB = PROJECT_ROOT / "graph" / "kuzu_db"


def _wipe_db(db_path: Path) -> None:
    if db_path.exists():
        if db_path.is_dir():
            shutil.rmtree(db_path)
        else:
            db_path.unlink()
    for sidecar in db_path.parent.glob(db_path.name + ".*"):
        if sidecar.is_file():
            sidecar.unlink()


def _print_graph_state(conn) -> None:
    s = compute_stats(conn)
    print(f"  graph : {s['total_nodes']} nodes, {s['total_rels']} rels")
    if s.get("node_counts"):
        by_type = "  |  ".join(f"{r['type']}={r['c']}" for r in s["node_counts"])
        print(f"  types : {by_type}")
    if s.get("rel_counts"):
        by_rel = "  |  ".join(f"{r['type']}={r['c']}" for r in s["rel_counts"])
        print(f"  rels  : {by_rel}")


def run(
    docs: list[Path],
    db_path: Path,
    out_dir: Path,
    wipe: bool = True,
) -> None:
    if wipe:
        _wipe_db(db_path)
        print(f"DB wiped: {db_path}")

    conn = connect(db_path)
    init_schema(conn)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "extraction_logs.jsonl"
    if log_path.exists():
        log_path.unlink()

    client = anthropic.Anthropic(api_key=extract_mod.load_api_key())
    skill_text = extract_mod.SKILL_PATH.read_text(encoding="utf-8")

    print(f"model  : {extract_mod.MODEL}")
    print(f"docs   : {len(docs)}")
    print(f"db     : {db_path}")
    print(f"output : {out_dir}\n")

    t_start = time.perf_counter()
    total_cost = 0.0
    total_tool_calls = 0

    for i, doc_path in enumerate(docs, 1):
        doc_id = doc_path.stem
        out_json = out_dir / f"{doc_id}.json"
        print(f"[{i}/{len(docs)}] {doc_id}")

        record = extract_mod.extract_one(
            client=client,
            skill_text=skill_text,
            doc_path=doc_path,
            doc_id=doc_id,
            out_path=out_json,
            conn=conn,
            log_path=log_path,
        )

        total_cost += record["total_cost_usd"]
        total_tool_calls += record.get("tool_calls", 0)

        if not record["success"]:
            print(f"  EXTRACT FAILED: {record['error']}\n")
            continue

        n = record["result"]["node_count"]
        r = record["result"]["rel_count"]
        tc = record["tool_calls"]
        print(
            f"  extracted: {n} nodes, {r} rels  "
            f"tool_calls={tc}  ${record['total_cost_usd']:.4f}  {record['duration_s']:.1f}s"
        )

        payload = json.loads(out_json.read_text(encoding="utf-8"))
        ingest_report = ingest_payload(conn, payload, log_root=EXPERIMENT_ROOT)

        rej_n = len(ingest_report.rejected_nodes)
        rej_r = len(ingest_report.rejected_rels)
        rew = len(ingest_report.rewritten_ids)
        pdups = len(ingest_report.potential_duplicates)
        extras = []
        if rej_n: extras.append(f"rej_nodes={rej_n}")
        if rej_r: extras.append(f"rej_rels={rej_r}")
        if rew: extras.append(f"rewritten={rew}")
        if pdups: extras.append(f"potential_dups={pdups}")
        extra_str = "  " + "  ".join(extras) if extras else ""
        print(
            f"  ingested: +{ingest_report.nodes_created}n / +{ingest_report.rels_created}r{extra_str}"
        )
        _print_graph_state(conn)
        print()

    elapsed = time.perf_counter() - t_start

    print("=" * 60)
    print("Final graph state")
    print("=" * 60)
    _print_graph_state(conn)

    audit = run_audit(conn)
    if audit.warnings:
        print("\nAudit warnings:")
        for w in audit.warnings:
            print(f"  ! {w}")
    if audit.errors:
        print("\nAudit errors:")
        for e in audit.errors:
            print(f"  X {e}")

    m = audit.metrics
    print(f"\nrelated_to ratio     : {m.get('related_to_ratio', 0):.1%}")
    print(f"orphan ratio         : {m.get('orphan_ratio', 0):.1%}")
    print(f"no-description ratio : {m.get('no_description_ratio', 0):.1%}")

    print(f"\nTotal cost    : ${total_cost:.4f}")
    print(f"Total tool calls: {total_tool_calls}")
    print(f"Wall clock    : {elapsed:.1f}s")
    print(f"Log           : {log_path}")


def main() -> None:
    p = argparse.ArgumentParser(description="Agentic ingestion — extract + ingest per document.")
    p.add_argument(
        "--docs", type=Path, nargs="+", required=True,
        help="Document paths to process (in order).",
    )
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument(
        "--out-dir", type=Path,
        default=EXPERIMENT_ROOT / "results" / "agentic",
    )
    p.add_argument(
        "--no-wipe", action="store_true",
        help="Do not wipe the DB before running (accumulate on existing graph).",
    )
    args = p.parse_args()

    missing = [d for d in args.docs if not d.exists()]
    if missing:
        sys.exit(f"documents not found: {', '.join(str(d) for d in missing)}")

    run(
        docs=args.docs,
        db_path=args.db,
        out_dir=args.out_dir,
        wipe=not args.no_wipe,
    )


if __name__ == "__main__":
    main()
