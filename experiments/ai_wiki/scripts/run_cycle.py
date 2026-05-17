#!/usr/bin/env python3
"""Run one full extraction + ingestion cycle on the corpus.

For each document:
  1. Call Claude (via extract.extract_one) in the chosen mode.
  2. Pipe the resulting JSON into brain.ingest_payload so subsequent
     preflight calls (cycle 2 only) can see the current graph state.

At the end, dumps:
  - results/<cycle>/graph_export.json
  - results/<cycle>/stats.json
  - results/<cycle>/audit_report.txt
  - results/<cycle>/cycle_summary.json
  - results/<cycle>/extraction_logs.jsonl (one line per doc)
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
from lib.export_import import write_export  # noqa: E402
from lib.ingest import ingest_payload  # noqa: E402
from lib.query import stats as compute_stats  # noqa: E402

import extract as extract_mod  # noqa: E402

DEFAULT_DB = PROJECT_ROOT / "graph" / "kuzu_db"


def _wipe_db(db_path: Path) -> None:
    """Remove the Kuzu DB whether it's a single file or a directory.

    Also removes any Kuzu sidecar files such as ``<db>.wal`` and
    ``<db>.shadow`` that may live next to it.
    """
    if db_path.exists():
        if db_path.is_dir():
            shutil.rmtree(db_path)
        else:
            db_path.unlink()
    for sidecar in db_path.parent.glob(db_path.name + ".*"):
        if sidecar.is_file():
            sidecar.unlink()


def _write_audit_report(report, out_path: Path) -> None:
    lines = ["# Audit report\n"]
    m = report.metrics
    lines.append(f"total nodes : {m.get('total_nodes', 0)}")
    lines.append(f"total rels  : {m.get('total_rels', 0)}\n")
    lines.append("## Nodes by type")
    for row in m.get("nodes_by_type", []):
        lines.append(f"  {row['type']:<14} {row['c']}")
    lines.append("\n## Rels by type")
    for row in m.get("rels_by_type", []):
        lines.append(f"  {row['type']:<14} {row['c']}")
    lines.append("")
    lines.append(f"related_to ratio    : {m.get('related_to_ratio', 0):.2%}")
    lines.append(f"no-description ratio: {m.get('no_description_ratio', 0):.2%}")
    lines.append(f"orphan ratio        : {m.get('orphan_ratio', 0):.2%}")
    lines.append(f"single-source rels  : {m.get('single_source_rels', 0)}\n")
    lines.append("## Avg confidence by rel type")
    for row in m.get("confidence_by_rel_type", []):
        lines.append(f"  {row['type']:<14} avg={row['avg_conf']:.2f}  (n={row['c']})")
    lines.append("\n## Top out-degree")
    for row in m.get("top_out_degree", []):
        lines.append(f"  {row['id']:<40} {row['deg']}")
    lines.append("\n## Top in-degree")
    for row in m.get("top_in_degree", []):
        lines.append(f"  {row['id']:<40} {row['deg']}")
    if report.warnings:
        lines.append("\n## Warnings")
        for w in report.warnings:
            lines.append(f"  ! {w}")
    if report.errors:
        lines.append("\n## Errors")
        for e in report.errors:
            lines.append(f"  X {e}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_cycle(
    corpus_dirs: list[Path],
    out_dir: Path,
    extractions_dir: Path,
    mode: str,
    db_path: Path,
    limit: int = 0,
    force_extract: bool = False,
    context_limit: int = 8,
    context_neighbors: int = 4,
) -> dict:
    _wipe_db(db_path)
    conn = connect(db_path)
    init_schema(conn)
    extractions_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "extraction_logs.jsonl"
    if log_path.exists():
        log_path.unlink()

    client = anthropic.Anthropic(api_key=extract_mod.load_api_key())
    skill_text = extract_mod.SKILL_PATH.read_text(encoding="utf-8")

    docs: list[Path] = []
    for d in corpus_dirs:
        docs.extend(sorted(d.glob("*.md")))
    if limit > 0:
        docs = docs[:limit]
    if not docs:
        sys.exit("no documents found")

    print(f"cycle  : {mode}")
    print(f"docs   : {len(docs)}")
    print(f"db     : {db_path}")
    print(f"output : {out_dir}\n")

    t_start = time.perf_counter()
    totals = {"cost": 0.0, "duration": 0.0, "success": 0, "failed": 0,
              "nodes_ingested": 0, "rels_ingested": 0}

    for i, doc_path in enumerate(docs, 1):
        doc_id = doc_path.stem
        out_json = extractions_dir / f"{doc_id}.json"

        record = extract_mod.extract_one(
            client=client,
            skill_text=skill_text,
            doc_path=doc_path,
            doc_id=doc_id,
            out_path=out_json,
            mode=mode,
            db_path=db_path,
            context_limit=context_limit,
            context_neighbors=context_neighbors,
            log_path=log_path,
        )
        totals["cost"] += record["total_cost_usd"]
        totals["duration"] += record["duration_s"]

        if not record["success"]:
            totals["failed"] += 1
            print(f"  [{i:>2}/{len(docs)}] {doc_id}  EXTRACT FAILED  {record['error']}")
            continue

        # Ingest into the DB so the next preflight (if any) sees the state.
        payload = json.loads(out_json.read_text(encoding="utf-8"))
        ingest_report = ingest_payload(conn, payload, log_root=EXPERIMENT_ROOT)
        totals["nodes_ingested"] += ingest_report.nodes_created
        totals["rels_ingested"] += ingest_report.rels_created
        totals["success"] += 1

        rej_n = len(ingest_report.rejected_nodes)
        rej_r = len(ingest_report.rejected_rels)
        rew = len(ingest_report.rewritten_ids)
        pdups = len(ingest_report.potential_duplicates)
        extras = []
        if rej_n: extras.append(f"rej_n={rej_n}")
        if rej_r: extras.append(f"rej_r={rej_r}")
        if rew: extras.append(f"rew={rew}")
        if pdups: extras.append(f"pdup={pdups}")
        extra_str = "  " + " ".join(extras) if extras else ""
        print(
            f"  [{i:>2}/{len(docs)}] {doc_id}  ok  "
            f"+{ingest_report.nodes_created}n/+{ingest_report.rels_created}r  "
            f"${record['total_cost_usd']:.4f}  {record['duration_s']:.1f}s{extra_str}"
        )

    elapsed = time.perf_counter() - t_start

    # Final audit + stats + export
    audit = run_audit(conn)
    write_export(conn, out_dir / "graph_export.json")
    _write_audit_report(audit, out_dir / "audit_report.txt")

    stats_data = compute_stats(conn)
    (out_dir / "stats.json").write_text(
        json.dumps({**stats_data, "audit_metrics": audit.metrics}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = {
        "mode": mode,
        "docs_processed": len(docs),
        "success": totals["success"],
        "failed": totals["failed"],
        "total_cost_usd": round(totals["cost"], 6),
        "total_extract_duration_s": round(totals["duration"], 3),
        "wall_clock_s": round(elapsed, 3),
        "graph": {
            "total_nodes": stats_data["total_nodes"],
            "total_rels": stats_data["total_rels"],
            "nodes_by_type": stats_data["node_counts"],
            "rels_by_type": stats_data["rel_counts"],
        },
        "audit_warnings": audit.warnings,
        "audit_errors": audit.errors,
    }
    (out_dir / "cycle_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\nFinal: {stats_data['total_nodes']} nodes, {stats_data['total_rels']} rels")
    print(f"Total cost ${totals['cost']:.4f}, wall {elapsed:.1f}s")
    if audit.warnings:
        print(f"Audit warnings: {len(audit.warnings)}")
        for w in audit.warnings:
            print(f"  ! {w}")

    return summary


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["no_preflight", "with_preflight"], required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--extractions-dir", type=Path, required=True)
    p.add_argument(
        "--corpus", type=Path, action="append", required=True,
        help="One or more corpus directories (can be repeated).",
    )
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--limit", type=int, default=0, help="Process only the first N docs.")
    p.add_argument("--force-extract", action="store_true",
                   help="Re-extract even when an output JSON already exists (currently always re-extracts).")
    args = p.parse_args()
    run_cycle(
        corpus_dirs=args.corpus,
        out_dir=args.out_dir,
        extractions_dir=args.extractions_dir,
        mode=args.mode,
        db_path=args.db,
        limit=args.limit,
        force_extract=args.force_extract,
    )


if __name__ == "__main__":
    main()
