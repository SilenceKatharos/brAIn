#!/usr/bin/env python3
"""Generate a markdown comparison report between two cycle output directories."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def load_cycle(path: Path) -> dict:
    summary = json.loads((path / "cycle_summary.json").read_text(encoding="utf-8"))
    stats = json.loads((path / "stats.json").read_text(encoding="utf-8"))
    export = json.loads((path / "graph_export.json").read_text(encoding="utf-8"))
    logs = []
    log_file = path / "extraction_logs.jsonl"
    if log_file.exists():
        for line in log_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                logs.append(json.loads(line))
    return {"summary": summary, "stats": stats, "export": export, "logs": logs}


def degree_map(export: dict) -> dict[str, int]:
    deg: dict[str, int] = defaultdict(int)
    for r in export["rels"]:
        deg[r["src"]] += 1
        deg[r["dst"]] += 1
    return deg


def neighbors_of(export: dict, node_id: str) -> dict:
    out, inc = [], []
    for r in export["rels"]:
        if r["src"] == node_id:
            out.append({"type": r["type"], "dst": r["dst"]})
        if r["dst"] == node_id:
            inc.append({"type": r["type"], "src": r["src"]})
    return {"outgoing": out, "incoming": inc}


def fmt_pct(x: float) -> str:
    return f"{x*100:.1f}%"


def render(cycle1: dict, cycle2: dict, watch_concepts: list[str]) -> str:
    s1, s2 = cycle1["summary"], cycle2["summary"]
    g1, g2 = s1["graph"], s2["graph"]
    a1 = cycle1["stats"]["audit_metrics"]
    a2 = cycle2["stats"]["audit_metrics"]
    e1, e2 = cycle1["export"], cycle2["export"]
    d1, d2 = degree_map(e1), degree_map(e2)
    nodes1 = {n["id"] for n in e1["nodes"]}
    nodes2 = {n["id"] for n in e2["nodes"]}

    def cost_breakdown(logs):
        pre = sum(l.get("preflight", {}).get("skim_cost_usd", 0) for l in logs if l.get("preflight"))
        ex = sum(l.get("extraction", {}).get("cost_usd", 0) for l in logs)
        return pre, ex, pre + ex

    pre1, ex1, tot1 = cost_breakdown(cycle1["logs"])
    pre2, ex2, tot2 = cost_breakdown(cycle2["logs"])

    avg_deg1 = (sum(d1.values()) / len(nodes1)) if nodes1 else 0
    avg_deg2 = (sum(d2.values()) / len(nodes2)) if nodes2 else 0

    lines: list[str] = []
    lines += [
        "# brAIn — Cycle 1 vs Cycle 2 comparison",
        "",
        f"- **Cycle 1 (no preflight)**: {s1['docs_processed']} docs, {s1['success']} ok, {s1['failed']} failed",
        f"- **Cycle 2 (with preflight)**: {s2['docs_processed']} docs, {s2['success']} ok, {s2['failed']} failed",
        "",
        "## Headline metrics",
        "",
        "| Metric | Cycle 1 (baseline) | Cycle 2 (preflight) | Δ |",
        "|---|---:|---:|---|",
        f"| Total nodes | {g1['total_nodes']} | {g2['total_nodes']} | {g2['total_nodes']-g1['total_nodes']:+d} ({fmt_pct((g2['total_nodes']-g1['total_nodes'])/max(g1['total_nodes'],1))}) |",
        f"| Total rels | {g1['total_rels']} | {g2['total_rels']} | {g2['total_rels']-g1['total_rels']:+d} ({fmt_pct((g2['total_rels']-g1['total_rels'])/max(g1['total_rels'],1))}) |",
        f"| Avg degree (in+out) | {avg_deg1:.2f} | {avg_deg2:.2f} | {avg_deg2-avg_deg1:+.2f} |",
        f"| related_to ratio | {fmt_pct(a1.get('related_to_ratio', 0))} | {fmt_pct(a2.get('related_to_ratio', 0))} | — |",
        f"| Orphan ratio | {fmt_pct(a1.get('orphan_ratio', 0))} | {fmt_pct(a2.get('orphan_ratio', 0))} | — |",
        f"| No-description ratio | {fmt_pct(a1.get('no_description_ratio', 0))} | {fmt_pct(a2.get('no_description_ratio', 0))} | — |",
        f"| Total cost (USD) | ${tot1:.4f} | ${tot2:.4f} | +${tot2-tot1:.4f} |",
        f"| Extract wall-clock (s) | {s1['total_extract_duration_s']:.1f} | {s2['total_extract_duration_s']:.1f} | {s2['total_extract_duration_s']-s1['total_extract_duration_s']:+.1f}s |",
        "",
        "## Cost breakdown",
        "",
        "| | Preflight (skim) | Extraction | Total |",
        "|---|---:|---:|---:|",
        f"| Cycle 1 | — | ${ex1:.4f} | ${tot1:.4f} |",
        f"| Cycle 2 | ${pre2:.4f} | ${ex2:.4f} | ${tot2:.4f} |",
        "",
        "## Node-type distribution",
        "",
        "| Type | Cycle 1 | Cycle 2 |",
        "|---|---:|---:|",
    ]
    types = sorted({r["type"] for r in g1["nodes_by_type"]} | {r["type"] for r in g2["nodes_by_type"]})
    c1_by_type = {r["type"]: r["c"] for r in g1["nodes_by_type"]}
    c2_by_type = {r["type"]: r["c"] for r in g2["nodes_by_type"]}
    for t in types:
        lines.append(f"| {t} | {c1_by_type.get(t, 0)} | {c2_by_type.get(t, 0)} |")
    lines += ["", "## Relation-type distribution", "",
              "| Type | Cycle 1 | Cycle 2 |", "|---|---:|---:|"]
    rtypes = sorted({r["type"] for r in g1["rels_by_type"]} | {r["type"] for r in g2["rels_by_type"]})
    r1_by_type = {r["type"]: r["c"] for r in g1["rels_by_type"]}
    r2_by_type = {r["type"]: r["c"] for r in g2["rels_by_type"]}
    for t in rtypes:
        lines.append(f"| {t} | {r1_by_type.get(t, 0)} | {r2_by_type.get(t, 0)} |")

    lines += ["", "## Top out-degree", "", "| Cycle 1 | Cycle 2 |", "|---|---|"]
    top_out_1 = a1.get("top_out_degree", [])[:10]
    top_out_2 = a2.get("top_out_degree", [])[:10]
    for i in range(max(len(top_out_1), len(top_out_2))):
        l = top_out_1[i] if i < len(top_out_1) else None
        r = top_out_2[i] if i < len(top_out_2) else None
        lc = f"{l['id']} ({l['deg']})" if l else ""
        rc = f"{r['id']} ({r['deg']})" if r else ""
        lines.append(f"| {lc} | {rc} |")

    lines += ["", "## Concept neighborhood comparison", ""]
    for concept in watch_concepts:
        candidates_1 = [nid for nid in nodes1 if concept in nid]
        candidates_2 = [nid for nid in nodes2 if concept in nid]
        lines.append(f"### `{concept}`")
        lines.append("")
        lines.append(f"- Cycle 1 candidates ({len(candidates_1)}): {', '.join(candidates_1) or '(none)'}")
        lines.append(f"- Cycle 2 candidates ({len(candidates_2)}): {', '.join(candidates_2) or '(none)'}")
        # focus on the most-connected variant in each cycle
        best_1 = max(candidates_1, key=lambda n: d1.get(n, 0), default=None)
        best_2 = max(candidates_2, key=lambda n: d2.get(n, 0), default=None)
        if best_1:
            n1 = neighbors_of(e1, best_1)
            lines.append(f"- Cycle 1 hub `{best_1}` (deg={d1.get(best_1, 0)}):")
            for x in n1["outgoing"][:5]:
                lines.append(f"    `{best_1}` --[{x['type']}]--> `{x['dst']}`")
            for x in n1["incoming"][:5]:
                lines.append(f"    `{x['src']}` --[{x['type']}]--> `{best_1}`")
        if best_2:
            n2 = neighbors_of(e2, best_2)
            lines.append(f"- Cycle 2 hub `{best_2}` (deg={d2.get(best_2, 0)}):")
            for x in n2["outgoing"][:5]:
                lines.append(f"    `{best_2}` --[{x['type']}]--> `{x['dst']}`")
            for x in n2["incoming"][:5]:
                lines.append(f"    `{x['src']}` --[{x['type']}]--> `{best_2}`")
        lines.append("")

    lines += ["## Audit warnings", ""]
    lines.append("**Cycle 1**")
    for w in s1.get("audit_warnings", []) or ["(none)"]:
        lines.append(f"- {w}")
    lines.append("")
    lines.append("**Cycle 2**")
    for w in s2.get("audit_warnings", []) or ["(none)"]:
        lines.append(f"- {w}")
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cycle1", type=Path, required=True)
    p.add_argument("--cycle2", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--watch", nargs="*", default=[
        "neural_network", "backpropagation", "attention", "transformer",
        "gradient_descent", "overfitting", "reinforcement", "supervised",
    ])
    args = p.parse_args()
    c1 = load_cycle(args.cycle1)
    c2 = load_cycle(args.cycle2)
    md = render(c1, c2, args.watch)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(md, encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
