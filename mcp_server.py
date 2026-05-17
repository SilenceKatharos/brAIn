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
            "The file must follow the brAIn ingestion schema "
            "(doc_id, nodes[], rels[]). Re-ingesting the same doc_id is safe and idempotent."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the JSON payload file"},
            },
            "required": ["path"],
        },
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
        report = ingest_payload(conn, payload, log_root=LOG_ROOT)
        return (
            f"doc_id: {report.doc_id}\n"
            f"  nodes: {report.nodes_created} created, {report.nodes_updated} updated\n"
            f"  rels : {report.rels_created} created, {report.rels_updated} updated\n"
            f"  skipped rels: {len(report.skipped_rels)}"
        )

    return f"Unknown tool: {name}"


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
