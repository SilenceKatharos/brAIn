#!/usr/bin/env python3
"""brAIn MCP server — exposes brain.py query functions as Claude tools.

Registered in ~/.claude/settings.json so tools are available in every
Claude Code session regardless of the working directory.

Tools: brain_find, brain_show, brain_causes, brain_effects, brain_paths,
       brain_stats, brain_query, brain_ingest
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

import kuzu
import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

from lib.db import connect, init_schema
from lib.ingest import ingest_payload
from lib import audit as audit_mod
from lib import check as check_mod
from lib import query as q

DB_PATH = PROJECT_ROOT / "graph" / "kuzu_db"
LOG_ROOT = PROJECT_ROOT


def _conn(read_only: bool = True) -> kuzu.Connection:
    db = kuzu.Database(str(DB_PATH), read_only=read_only)
    return kuzu.Connection(db)


# ── formatters (mirror brain.py CLI output) ──────────────────────────────────

def _fmt_find(results: list[dict]) -> str:
    if not results:
        return "(no match)"
    lines = []
    for r in results:
        lines.append(f"- {r['id']} [{r['type']}] {r['label']}")
        if r.get("description"):
            lines.append(f"    {r['description']}")
    return "\n".join(lines)


def _fmt_show(node: dict | None) -> str:
    if not node:
        return "(node not found)"
    lines = [
        f"# {node['label']} ({node['id']})",
        f"  type        : {node['type']}",
        f"  description : {node['description']}",
        f"  importance  : {node['importance']}",
        f"  sources     : {', '.join(node['sources']) or '-'}",
        "",
        "## Outgoing",
    ]
    if not node.get("outgoing"):
        lines.append("  (none)")
    for e in node.get("outgoing", []):
        factors = [f for f in (e.get("factors") or []) if f]
        factor_str = f" f={factors[0]}" if factors else ""
        lines.append(f"  --[{e['type']} c={e['confidence']:.2f}{factor_str}]--> {e['dst']} ({e['dst_label']})")
        for ev in e.get("evidences", []):
            if ev:
                lines.append(f"      « {ev} »")
    lines.append("")
    lines.append("## Incoming")
    if not node.get("incoming"):
        lines.append("  (none)")
    for e in node.get("incoming", []):
        factors = [f for f in (e.get("factors") or []) if f]
        factor_str = f" f={factors[0]}" if factors else ""
        lines.append(f"  --[{e['type']} c={e['confidence']:.2f}{factor_str}]--> from {e['src']} ({e['src_label']})")
        for ev in e.get("evidences", []):
            if ev:
                lines.append(f"      « {ev} »")
    return "\n".join(lines)


def _fmt_chain(levels: list[list[dict]], label: str) -> str:
    if not levels:
        return f"{label}\n(no chain found)"
    lines = [label, ""]
    for i, level in enumerate(levels, 1):
        lines.append(f"-- level {i} --")
        for row in level:
            factors = [f for f in (row.get("factors") or []) if f]
            factor_str = f" f={factors[0]}" if factors else ""
            lines.append(
                f"  {row['src']} ({row['src_label']}) "
                f"--[{row['type']} c={row['confidence']:.2f}{factor_str}]--> "
                f"{row['dst']} ({row['dst_label']})"
            )
            for ev in row.get("evidences", []):
                if ev:
                    lines.append(f"      « {ev} »")
    return "\n".join(lines)


def _fmt_paths(path_list: list[dict]) -> str:
    if not path_list:
        return "(no path found)"
    lines = []
    for i, p in enumerate(path_list, 1):
        parts = []
        for j, node in enumerate(p["nodes"]):
            parts.append(node["label"] or node["id"])
            if j < len(p["rels"]):
                r = p["rels"][j]
                parts.append(f"--[{r['type']} c={r['confidence']:.2f}]-->")
        lines.append(f"{i}. " + " ".join(parts))
    return "\n".join(lines)


def _fmt_stats(s: dict) -> str:
    lines = ["# Nodes"]
    for row in s["node_counts"]:
        lines.append(f"  {row['type']:<14} {row['c']}")
    lines += ["", "# Relations"]
    for row in s["rel_counts"]:
        lines.append(f"  {row['type']:<16} {row['c']}")
    lines.append(f"\nTotal: {s['total_nodes']} nodes, {s['total_rels']} rels")
    return "\n".join(lines)


# ── MCP server ────────────────────────────────────────────────────────────────

server = Server("brain")

TOOLS = [
    types.Tool(
        name="brain_find",
        description=(
            "Search the brAIn knowledge graph by keyword (id or label substring). "
            "Use this to check whether a concept already exists before creating a node, "
            "or to retrieve context about a topic."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword or phrase to search for"},
                "limit": {"type": "integer", "description": "Max results (default 20)", "default": 20},
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="brain_show",
        description=(
            "Show a node's full detail: description, type, importance, "
            "and all outgoing/incoming relations with evidence."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Exact node id (snake_case)"},
            },
            "required": ["node_id"],
        },
    ),
    types.Tool(
        name="brain_causes",
        description="Walk upstream: what causes / requires / enables the given node.",
        inputSchema={
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "depth": {"type": "integer", "default": 3},
            },
            "required": ["node_id"],
        },
    ),
    types.Tool(
        name="brain_effects",
        description="Walk downstream: what does the given node cause / enable / precede.",
        inputSchema={
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "depth": {"type": "integer", "default": 3},
            },
            "required": ["node_id"],
        },
    ),
    types.Tool(
        name="brain_paths",
        description="Find causal paths between two nodes.",
        inputSchema={
            "type": "object",
            "properties": {
                "src": {"type": "string", "description": "Source node id"},
                "dst": {"type": "string", "description": "Destination node id"},
            },
            "required": ["src", "dst"],
        },
    ),
    types.Tool(
        name="brain_stats",
        description="Return node and relation counts by type.",
        inputSchema={"type": "object", "properties": {}},
    ),
    types.Tool(
        name="brain_query",
        description="Run a raw Cypher query against the graph. Use for advanced filtering.",
        inputSchema={
            "type": "object",
            "properties": {
                "cypher": {"type": "string", "description": "Cypher query string"},
            },
            "required": ["cypher"],
        },
    ),
    types.Tool(
        name="brain_ingest",
        description=(
            "Ingest a JSON payload file into the graph. "
            "RESERVED USAGE: only the background sync agent (brain_sync_agent.sh) "
            "and explicit user-invoked /ingest commands should call this. Working "
            "sessions must NOT call brain_ingest spontaneously — graph maintenance "
            "is the sync agent's job and runs automatically at every Stop. If unsure, "
            "default to NOT calling. "
            "STRICT: runs check() first and refuses payloads with errors (missing "
            "endpoints, zero causal edges, rejected nodes/rels). No --force here — "
            "fix the payload and retry. Re-ingesting the same doc_id is idempotent."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the JSON payload file"},
            },
            "required": ["path"],
        },
    ),
    types.Tool(
        name="brain_check",
        description=(
            "Dry-run validation of a payload before ingest. Returns the list of errors and warnings "
            "(missing endpoints, slug pitfalls, paragraph descriptions, weak evidence, missing causal edges, "
            "potential duplicates against the existing graph). Use this before calling brain_ingest to know "
            "what to fix."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the JSON payload file"},
            },
            "required": ["path"],
        },
    ),
    types.Tool(
        name="brain_audit",
        description=(
            "Health audit of the graph: volumes, related_to ratio, causal/structural balance, "
            "orphan ratio, average confidence by rel type, contributions per doc. Returns warnings "
            "and errors so you can see if recent ingests degraded the graph."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
]


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        text = _dispatch(name, arguments)
    except Exception as exc:
        text = f"Error: {exc}"
    return [types.TextContent(type="text", text=text)]


def _dispatch(name: str, args: dict) -> str:
    if name == "brain_find":
        conn = _conn()
        results = q.find_nodes(conn, args["query"], limit=args.get("limit", 20))
        return _fmt_find(results)

    if name == "brain_show":
        conn = _conn()
        node = q.show_node(conn, args["node_id"])
        return _fmt_show(node)

    if name == "brain_causes":
        conn = _conn()
        levels = q.causes_of(conn, args["node_id"], depth=args.get("depth", 3))
        return _fmt_chain(levels, f"Upstream chain leading to {args['node_id']}")

    if name == "brain_effects":
        conn = _conn()
        levels = q.effects_of(conn, args["node_id"], depth=args.get("depth", 3))
        return _fmt_chain(levels, f"Downstream chain from {args['node_id']}")

    if name == "brain_paths":
        conn = _conn()
        path_list = q.paths(conn, args["src"], args["dst"])
        return _fmt_paths(path_list)

    if name == "brain_stats":
        conn = _conn()
        return _fmt_stats(q.stats(conn))

    if name == "brain_query":
        conn = _conn()
        results = q.run_cypher(conn, args["cypher"])
        return json.dumps(results, ensure_ascii=False, indent=2)

    if name == "brain_ingest":
        path = Path(args["path"])
        if not path.exists():
            return f"File not found: {path}"
        payload = json.loads(path.read_text(encoding="utf-8"))
        conn = _conn(read_only=False)
        init_schema(conn)
        pre = check_mod.check_payload(payload, conn)
        if pre.has_errors:
            return (
                f"INGEST REFUSED (doc_id={pre.doc_id}): the payload has error-level issues. "
                f"Fix them in the JSON file and retry. The MCP path is strict by design — there is no --force.\n\n"
                + _fmt_check(pre)
            )
        report = ingest_payload(conn, payload, log_root=LOG_ROOT)
        lines = [
            f"doc_id: {report.doc_id}",
            f"  project tag    : {report.project_tag_injected or '-'}",
            f"  nodes: {report.nodes_created} created, {report.nodes_updated} updated",
            f"  rels : {report.rels_created} created, {report.rels_updated} updated",
        ]
        if report.skipped_rels:
            lines.append(f"  X skipped rels: {len(report.skipped_rels)} — DATA LOSS")
        if report.lint_issues:
            lines.append(f"  ! lint warnings: {len(report.lint_issues)}")
        if pre.has_warnings:
            lines.append("")
            lines.append(_fmt_check(pre))
        # post-ingest mini-audit
        audit_report = audit_mod.run_audit(conn)
        m = audit_report.metrics
        lines.append("")
        lines.append("# Post-ingest health")
        lines.append(f"  total: {m['total_nodes']} nodes, {m['total_rels']} rels")
        lines.append(
            f"  causal {m.get('causal_ratio', 0):.0%} | "
            f"structural {m.get('structural_dominance', 0):.0%} | "
            f"tradeoff {m.get('tradeoff_ratio', 0):.0%} | "
            f"orphans {m.get('orphan_ratio', 0):.0%}"
        )
        for w in audit_report.warnings:
            lines.append(f"  ! {w}")
        return "\n".join(lines)

    if name == "brain_check":
        path = Path(args["path"])
        if not path.exists():
            return f"File not found: {path}"
        payload = json.loads(path.read_text(encoding="utf-8"))
        conn = _conn()
        pre = check_mod.check_payload(payload, conn)
        return _fmt_check(pre)

    if name == "brain_audit":
        conn = _conn()
        report = audit_mod.run_audit(conn)
        return _fmt_audit(report)

    return f"Unknown tool: {name}"


def _fmt_check(rep: check_mod.CheckReport) -> str:
    lines = [f"doc_id: {rep.doc_id}"]
    lines.append(f"  payload: {rep.node_count} node(s), {rep.rel_count} rel(s)")
    if rep.project_tag:
        lines.append(f"  project tag (would be auto-injected): {rep.project_tag}")
    if rep.rejected_nodes:
        lines.append(f"  X {len(rep.rejected_nodes)} node(s) rejected")
        for rn in rep.rejected_nodes[:5]:
            lines.append(f"    - {rn.get('reason')}: {rn.get('raw', {}).get('label', '?')}")
    if rep.rejected_rels:
        lines.append(f"  X {len(rep.rejected_rels)} rel(s) rejected")
        for rr in rep.rejected_rels[:5]:
            lines.append(f"    - {rr.get('reason')}: {rr.get('raw', {}).get('src')} -> {rr.get('raw', {}).get('dst')}")
    if rep.missing_endpoints:
        lines.append(f"  X {len(rep.missing_endpoints)} rel(s) reference missing nodes (slug pitfall?)")
        for me in rep.missing_endpoints[:5]:
            missing = "src" if me["src_missing"] else ""
            if me["dst_missing"]:
                missing = (missing + "+dst") if missing else "dst"
            lines.append(f"    - {me['src']} --[{me['type']}]--> {me['dst']}  (missing: {missing})")
    if not rep.causal_check_passed:
        lines.append(f"  X causal check failed: {rep.causal_check_reason}")
    if rep.rewritten_ids:
        lines.append(f"  ! {len(rep.rewritten_ids)} id(s) rewritten by slugify:")
        for ri in rep.rewritten_ids[:5]:
            lines.append(f"    - '{ri['proposed']}' -> '{ri['canonical']}' (label: {ri['label']})")
    if rep.lint_issues:
        lines.append(f"  ! {len(rep.lint_issues)} lint warning(s)")
        for li in rep.lint_issues[:8]:
            lines.append(f"    - [{li.kind}] {li.target}: {li.detail}")
    if rep.potential_duplicates:
        lines.append(f"  ! {len(rep.potential_duplicates)} potential duplicate(s) against existing graph")
        for pd in rep.potential_duplicates[:5]:
            cand_str = ", ".join(c["id"] for c in pd["candidates"][:3])
            lines.append(f"    - new '{pd['new_id']}' ('{pd['new_label']}') ~ existing: {cand_str}")
    if rep.has_errors:
        lines.append("\nresult: FAIL — fix errors before ingest")
    elif rep.has_warnings:
        lines.append("\nresult: PASS with warnings (ingest will proceed)")
    else:
        lines.append("\nresult: PASS — payload is clean")
    return "\n".join(lines)


def _fmt_audit(rep: "audit_mod.AuditReport") -> str:
    m = rep.metrics
    lines = [
        "# Volumes",
        f"  total nodes : {m['total_nodes']}",
        f"  total rels  : {m['total_rels']}",
        "",
        "# Health",
        f"  related_to ratio    : {m.get('related_to_ratio', 0):.1%}",
        f"  no-description ratio: {m.get('no_description_ratio', 0):.1%}",
        f"  orphan ratio        : {m.get('orphan_ratio', 0):.1%}",
        f"  single-source rels  : {m.get('single_source_rels', 0)}",
        f"  causal ratio        : {m.get('causal_ratio', 0):.1%}",
        f"  structural dominance: {m.get('structural_dominance', 0):.1%}",
        f"  tradeoff ratio      : {m.get('tradeoff_ratio', 0):.1%}",
    ]
    if rep.warnings:
        lines.append("")
        lines.append("# Warnings")
        for w in rep.warnings:
            lines.append(f"  ! {w}")
    if rep.errors:
        lines.append("")
        lines.append("# Errors")
        for e in rep.errors:
            lines.append(f"  X {e}")
    return "\n".join(lines)


async def main() -> None:
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
