#!/usr/bin/env python3
"""brAIn — causal knowledge graph CLI.

Plumbing tool: parses commands, calls into :mod:`lib`, prints results.
All semantic logic lives in :mod:`lib`; this file stays under 500 lines.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from lib import audit as audit_mod
from lib import check as check_mod
from lib import export_import
from lib import ingest as ingest_mod
from lib import merge as merge_mod
from lib import query as query_mod
from lib.db import DEFAULT_DB_PATH, connect, init_schema

PROJECT_ROOT = Path(__file__).resolve().parent
LOG_ROOT = PROJECT_ROOT


def _conn(db_path: Path | None):
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    return connect(db_path)


def _print_json(data) -> None:
    click.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--db", "db_path", type=click.Path(path_type=Path), default=None,
    help="Path to the Kuzu database directory (defaults to ./graph/kuzu_db).",
)
@click.pass_context
def cli(ctx: click.Context, db_path: Path | None) -> None:
    """brAIn — a causal knowledge graph CLI backed by Kuzu."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db_path


@cli.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Create the database and tables (idempotent)."""
    conn = _conn(ctx.obj["db_path"])
    init_schema(conn)
    target = ctx.obj["db_path"] or DEFAULT_DB_PATH
    click.echo(f"initialized: {target}")


def _print_check_report(rep: check_mod.CheckReport, prefix: str = "") -> None:
    """Print a CheckReport to stderr with clear severity markers."""
    if rep.project_tag:
        click.echo(f"{prefix}project tag auto-injected: {rep.project_tag}", err=True)
    if rep.rejected_nodes:
        click.echo(f"{prefix}X {len(rep.rejected_nodes)} node(s) rejected:", err=True)
        for rn in rep.rejected_nodes[:5]:
            click.echo(f"    - {rn.get('reason')}: {rn.get('raw', {}).get('label', '?')}", err=True)
    if rep.rejected_rels:
        click.echo(f"{prefix}X {len(rep.rejected_rels)} rel(s) rejected:", err=True)
        for rr in rep.rejected_rels[:5]:
            click.echo(f"    - {rr.get('reason')}: {rr.get('raw', {}).get('src')} -> {rr.get('raw', {}).get('dst')}", err=True)
    if rep.missing_endpoints:
        click.echo(f"{prefix}X {len(rep.missing_endpoints)} rel(s) reference missing nodes (slug pitfall?):", err=True)
        for me in rep.missing_endpoints[:5]:
            missing = "src" if me["src_missing"] else ""
            if me["dst_missing"]:
                missing = (missing + "+dst") if missing else "dst"
            click.echo(f"    - {me['src']} --[{me['type']}]--> {me['dst']}  (missing: {missing})", err=True)
    if not rep.causal_check_passed:
        click.echo(f"{prefix}X causal check failed: {rep.causal_check_reason}", err=True)
    if rep.rewritten_ids:
        click.echo(f"{prefix}! {len(rep.rewritten_ids)} id(s) rewritten by slugify:", err=True)
        for ri in rep.rewritten_ids[:5]:
            click.echo(f"    - '{ri['proposed']}' -> '{ri['canonical']}'  (label: {ri['label']})", err=True)
    if rep.lint_issues:
        click.echo(f"{prefix}! {len(rep.lint_issues)} lint warning(s):", err=True)
        for li in rep.lint_issues[:8]:
            click.echo(f"    - [{li.kind}] {li.target}: {li.detail}", err=True)
    if rep.potential_duplicates:
        click.echo(f"{prefix}! {len(rep.potential_duplicates)} potential duplicate(s) against existing graph:", err=True)
        for pd in rep.potential_duplicates[:5]:
            cand_str = ", ".join(c["id"] for c in pd["candidates"][:3])
            click.echo(f"    - new '{pd['new_id']}' ('{pd['new_label']}') ~ existing: {cand_str}", err=True)


@cli.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_context
def check(ctx: click.Context, file: Path) -> None:
    """Dry-run validation of a payload: schema, slugs, endpoints, causal balance, duplicates.

    Exits non-zero if any error-level issue is found.
    """
    conn = _conn(ctx.obj["db_path"])
    init_schema(conn)
    payload = json.loads(file.read_text(encoding="utf-8"))
    report = check_mod.check_payload(payload, conn)
    click.echo(f"doc_id: {report.doc_id}")
    click.echo(f"  payload: {report.node_count} node(s), {report.rel_count} rel(s)")
    _print_check_report(report, prefix="  ")
    if report.has_errors:
        click.echo("\nresult: FAIL — fix the errors above before ingest", err=True)
        sys.exit(2)
    if report.has_warnings:
        click.echo("\nresult: PASS with warnings (ingest would succeed)")
        sys.exit(1)
    click.echo("\nresult: PASS — payload is clean")


def _print_post_ingest_audit(conn) -> None:
    """Print a 5-line health summary after ingest. Surfaces causal/structural balance."""
    report = audit_mod.run_audit(conn)
    m = report.metrics
    click.echo("\n# Post-ingest health")
    click.echo(f"  total: {m['total_nodes']} nodes, {m['total_rels']} rels")
    click.echo(
        f"  causal {m.get('causal_ratio', 0):.0%} | "
        f"structural {m.get('structural_dominance', 0):.0%} | "
        f"tradeoff {m.get('tradeoff_ratio', 0):.0%} | "
        f"orphans {m.get('orphan_ratio', 0):.0%} | "
        f"related_to {m.get('related_to_ratio', 0):.0%}"
    )
    for w in report.warnings:
        click.echo(f"  ! {w}", err=True)
    for e in report.errors:
        click.echo(f"  X {e}", err=True)


def _refresh_known_projects_cache(conn) -> None:
    """Write /tmp/brain_known_projects.txt with one project name per line.

    Read by brain_user_prompt.sh on every user message to decide whether to
    inject the graph-first reminder. Refreshed after every ingest so a newly-
    registered project becomes detectable on the next turn without a kuzu
    round-trip from the hook.
    """
    from lib.db import rows as db_rows  # local import to keep top-level minimal
    try:
        result = db_rows(conn.execute(
            "MATCH (n:Node) UNWIND n.sources AS s "
            "WITH s WHERE s STARTS WITH 'project:' "
            "RETURN DISTINCT s"
        ))
        names = sorted({r["s"][len("project:"):] for r in result if r.get("s")})
        Path("/tmp/brain_known_projects.txt").write_text("\n".join(names) + "\n")
    except Exception:
        # Cache refresh is best-effort; never fail an ingest because of it.
        pass


@cli.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--force", is_flag=True, help="Ingest even if check finds error-level issues.")
@click.option("--no-causal-check", is_flag=True, help="Skip the causal-balance precondition.")
@click.pass_context
def ingest(ctx: click.Context, file: Path, force: bool, no_causal_check: bool) -> None:
    """Ingest nodes and relations from a JSON file.

    Runs ``brain check`` first and aborts unless --force is passed.
    Prints a post-ingest health summary so quality issues surface immediately.
    """
    conn = _conn(ctx.obj["db_path"])
    init_schema(conn)
    payload = json.loads(file.read_text(encoding="utf-8"))

    pre = check_mod.check_payload(payload, conn)
    if no_causal_check:
        pre.causal_check_passed = True
    if pre.has_errors and not force:
        click.echo(f"doc_id: {pre.doc_id}", err=True)
        click.echo("pre-ingest check FAILED — aborting (use --force to override):", err=True)
        _print_check_report(pre, prefix="  ")
        sys.exit(2)
    if pre.has_warnings:
        click.echo("pre-ingest warnings (ingest will proceed):", err=True)
        _print_check_report(pre, prefix="  ")
        click.echo("", err=True)

    report = ingest_mod.ingest_payload(conn, payload, log_root=LOG_ROOT)
    click.echo(f"doc_id: {report.doc_id}")
    if report.project_tag_injected:
        click.echo(f"  project tag    : {report.project_tag_injected} (auto-injected on every node/rel)")
    click.echo(
        f"  nodes: {report.nodes_created} created, {report.nodes_updated} updated"
    )
    click.echo(
        f"  rels : {report.rels_created} created, {report.rels_updated} updated"
    )
    if report.rels_purged or report.rels_purged_deleted:
        click.echo(
            f"  purge: {report.rels_purged} rel(s) trimmed, "
            f"{report.rels_purged_deleted} rel(s) deleted"
        )
    if report.rejected_nodes:
        click.echo(f"  X rejected nodes: {len(report.rejected_nodes)} (see extension_requests.jsonl)", err=True)
    if report.rejected_rels:
        click.echo(f"  X rejected rels : {len(report.rejected_rels)} (see extension_requests.jsonl)", err=True)
    if report.skipped_rels:
        click.echo(f"  X skipped rels (missing endpoint): {len(report.skipped_rels)} — DATA LOSS", err=True)
        for sk in report.skipped_rels[:5]:
            click.echo(f"      - {sk.get('src')} --[{sk.get('type')}]--> {sk.get('dst')}", err=True)
    if report.rewritten_ids:
        click.echo(f"  ! rewritten ids: {len(report.rewritten_ids)} (label had forbidden char or proposed id mismatch)", err=True)
    if report.potential_duplicates:
        click.echo(
            f"  ! potential duplicates: {len(report.potential_duplicates)} (see potential_duplicates.jsonl)",
            err=True,
        )

    _print_post_ingest_audit(conn)
    _refresh_known_projects_cache(conn)

    exit_code = 0
    if report.rejected_nodes or report.rejected_rels or report.skipped_rels:
        exit_code = 2
    elif report.lint_issues or report.rewritten_ids:
        exit_code = 1
    sys.exit(exit_code)


@cli.command()
@click.argument("pattern")
@click.option("--limit", type=int, default=30)
@click.pass_context
def find(ctx: click.Context, pattern: str, limit: int) -> None:
    """Search nodes by id or label substring (case-insensitive)."""
    conn = _conn(ctx.obj["db_path"])
    results = query_mod.find_nodes(conn, pattern, limit=limit)
    if not results:
        click.echo("(no match)")
        return
    for r in results:
        click.echo(f"- {r['id']} [{r['type']}] {r['label']}")
        if r["description"]:
            click.echo(f"    {r['description']}")


@cli.command()
@click.argument("node_id")
@click.pass_context
def show(ctx: click.Context, node_id: str) -> None:
    """Print a node and its incoming/outgoing edges."""
    conn = _conn(ctx.obj["db_path"])
    node = query_mod.show_node(conn, node_id)
    if not node:
        click.echo(f"(no node with id={node_id})")
        sys.exit(1)
    click.echo(f"# {node['label']} ({node['id']})")
    click.echo(f"  type        : {node['type']}")
    click.echo(f"  description : {node['description']}")
    click.echo(f"  importance  : {node['importance']}")
    click.echo(f"  sources     : {', '.join(node['sources']) or '-'}")
    click.echo("\n## Outgoing")
    if not node["outgoing"]:
        click.echo("  (none)")
    for e in node["outgoing"]:
        factors = [f for f in (e.get("factors") or []) if f]
        factor_str = f" f={factors[0]}" if factors else ""
        click.echo(
            f"  --[{e['type']} c={e['confidence']:.2f}{factor_str}]--> {e['dst']} ({e['dst_label']})"
        )
        for ev in e["evidences"]:
            if ev:
                click.echo(f"      « {ev} »")
    click.echo("\n## Incoming")
    if not node["incoming"]:
        click.echo("  (none)")
    for e in node["incoming"]:
        factors = [f for f in (e.get("factors") or []) if f]
        factor_str = f" f={factors[0]}" if factors else ""
        click.echo(
            f"  {e['src']} ({e['src_label']}) --[{e['type']} c={e['confidence']:.2f}{factor_str}]-->"
        )
        for ev in e["evidences"]:
            if ev:
                click.echo(f"      « {ev} »")


def _print_walk(start: str, levels) -> None:
    if not levels:
        click.echo("(no chain found)")
        return
    for i, level in enumerate(levels, 1):
        click.echo(f"\n-- level {i} --")
        for row in level:
            factors = [f for f in (row.get("factors") or []) if f]
            factor_str = f" f={factors[0]}" if factors else ""
            arrow = (
                f"{row['src']} ({row['src_label']}) --[{row['type']} "
                f"c={row['confidence']:.2f}{factor_str}]--> {row['dst']} ({row['dst_label']})"
            )
            click.echo(f"  {arrow}")
            for ev in row["evidences"]:
                if ev:
                    click.echo(f"      « {ev} »")


@cli.command()
@click.argument("node_id")
@click.option("--depth", type=int, default=3)
@click.pass_context
def causes(ctx: click.Context, node_id: str, depth: int) -> None:
    """Walk upstream causes/requires/enables/precedes from a node."""
    conn = _conn(ctx.obj["db_path"])
    click.echo(f"Upstream chain leading to {node_id}")
    _print_walk(node_id, query_mod.causes_of(conn, node_id, depth=depth))


@cli.command()
@click.argument("node_id")
@click.option("--depth", type=int, default=3)
@click.pass_context
def effects(ctx: click.Context, node_id: str, depth: int) -> None:
    """Walk downstream causes/enables/precedes from a node."""
    conn = _conn(ctx.obj["db_path"])
    click.echo(f"Downstream chain from {node_id}")
    _print_walk(node_id, query_mod.effects_of(conn, node_id, depth=depth))


@cli.command()
@click.argument("src")
@click.argument("dst")
@click.option("--max-hops", type=int, default=4)
@click.option("--limit", type=int, default=10)
@click.pass_context
def paths(ctx: click.Context, src: str, dst: str, max_hops: int, limit: int) -> None:
    """Find paths between two nodes (up to max-hops)."""
    conn = _conn(ctx.obj["db_path"])
    found = query_mod.paths(conn, src, dst, max_hops=max_hops, limit=limit)
    if not found:
        click.echo(f"(no path found within {max_hops} hops)")
        return
    for i, p in enumerate(found, 1):
        chain = []
        for k, n in enumerate(p["nodes"]):
            chain.append(n["id"])
            if k < len(p["rels"]):
                rel = p["rels"][k]
                chain.append(f" --[{rel['type']} c={rel['confidence']:.2f}]--> ")
        click.echo(f"{i}. {''.join(chain)}")


@cli.command()
@click.argument("topic")
@click.option("--limit", type=int, default=10, help="Max pivot nodes returned.")
@click.option("--neighbors", type=int, default=5, help="Top-K neighbors per pivot, each direction.")
@click.option(
    "--no-json",
    "human_readable",
    is_flag=True,
    help="Pretty text output instead of the default JSON.",
)
@click.pass_context
def context(ctx: click.Context, topic: str, limit: int, neighbors: int, human_readable: bool) -> None:
    """Return existing graph context around a topic (JSON by default).

    Designed to be called by extraction pipelines before sending a document
    to an LLM: pipe the JSON into the system prompt so the model can reuse
    existing ids instead of minting duplicates.
    """
    conn = _conn(ctx.obj["db_path"])
    result = query_mod.context_for_topic(conn, topic, limit=limit, neighbors=neighbors)
    if human_readable:
        click.echo(f"topic: {result['topic']} ({result['match_count']} match(es))")
        for m in result["matches"]:
            click.echo(f"\n# {m['label']} ({m['id']}) [{m['type']}] imp={m['importance']:.2f}")
            if m["description"]:
                click.echo(f"  {m['description']}")
            for e in m["outgoing"]:
                click.echo(
                    f"  --[{e['rel_type']} c={e['confidence']:.2f}]--> "
                    f"{e['dst']} ({e['dst_label']})"
                )
            for e in m["incoming"]:
                click.echo(
                    f"  {e['src']} ({e['src_label']}) "
                    f"--[{e['rel_type']} c={e['confidence']:.2f}]-->"
                )
        return
    _print_json(result)


@cli.command()
@click.argument("cypher")
@click.pass_context
def query(ctx: click.Context, cypher: str) -> None:
    """Execute a raw Cypher query."""
    conn = _conn(ctx.obj["db_path"])
    results = query_mod.run_cypher(conn, cypher)
    if not results:
        click.echo("(empty result)")
        return
    _print_json(results)


@cli.command()
@click.pass_context
def stats(ctx: click.Context) -> None:
    """Show counts grouped by node type and relation type."""
    conn = _conn(ctx.obj["db_path"])
    data = query_mod.stats(conn)
    click.echo("# Nodes")
    for row in data["node_counts"]:
        click.echo(f"  {row['type']:<12} {row['c']}")
    click.echo("\n# Relations")
    for row in data["rel_counts"]:
        click.echo(f"  {row['type']:<14} {row['c']}")
    click.echo(f"\nTotal: {data['total_nodes']} nodes, {data['total_rels']} rels")


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Emit the full report as JSON.")
@click.pass_context
def audit(ctx: click.Context, as_json: bool) -> None:
    """Health audit of the graph (volumes, ratios, orphans, etc.)."""
    conn = _conn(ctx.obj["db_path"])
    report = audit_mod.run_audit(conn)
    if as_json:
        _print_json({"metrics": report.metrics, "warnings": report.warnings, "errors": report.errors})
        sys.exit(report.exit_code)
    click.echo("# Volumes")
    click.echo(f"  total nodes : {report.metrics['total_nodes']}")
    click.echo(f"  total rels  : {report.metrics['total_rels']}")
    for row in report.metrics["nodes_by_type"]:
        click.echo(f"    node:{row['type']:<10} {row['c']}")
    for row in report.metrics["rels_by_type"]:
        click.echo(f"    rel:{row['type']:<12} {row['c']}")
    click.echo("\n# Health")
    click.echo(f"  related_to ratio    : {report.metrics.get('related_to_ratio', 0):.1%}")
    click.echo(f"  no-description ratio: {report.metrics.get('no_description_ratio', 0):.1%}")
    click.echo(f"  orphan ratio        : {report.metrics.get('orphan_ratio', 0):.1%}")
    click.echo(f"  single-source rels  : {report.metrics.get('single_source_rels', 0)}")
    click.echo(f"  causal ratio        : {report.metrics.get('causal_ratio', 0):.1%}")
    click.echo(f"  structural dominance: {report.metrics.get('structural_dominance', 0):.1%}")
    click.echo(f"  tradeoff ratio      : {report.metrics.get('tradeoff_ratio', 0):.1%}")
    if report.metrics["confidence_by_rel_type"]:
        click.echo("\n# Avg confidence by rel type")
        for row in report.metrics["confidence_by_rel_type"]:
            click.echo(f"  {row['type']:<14} avg={row['avg_conf']:.2f}  (n={row['c']})")
    if report.metrics["top_out_degree"]:
        click.echo("\n# Top out-degree")
        for row in report.metrics["top_out_degree"]:
            click.echo(f"  {row['id']:<30} {row['deg']}")
    if report.metrics["top_in_degree"]:
        click.echo("\n# Top in-degree")
        for row in report.metrics["top_in_degree"]:
            click.echo(f"  {row['id']:<30} {row['deg']}")
    if report.metrics["contributions_by_doc"]:
        click.echo("\n# Contributions by document")
        for row in report.metrics["contributions_by_doc"]:
            click.echo(
                f"  {row['doc_id']:<40} nodes={row['nodes']:<4} rels={row['rels']}"
            )
    if report.warnings:
        click.echo("\n# Warnings")
        for w in report.warnings:
            click.echo(f"  ! {w}")
    if report.errors:
        click.echo("\n# Errors")
        for e in report.errors:
            click.echo(f"  X {e}")
    sys.exit(report.exit_code)


@cli.command()
@click.argument("file", type=click.Path(dir_okay=False, path_type=Path))
@click.pass_context
def export(ctx: click.Context, file: Path) -> None:
    """Dump the full graph to a JSON file."""
    conn = _conn(ctx.obj["db_path"])
    export_import.write_export(conn, file)
    click.echo(f"exported to {file}")


@cli.command("import")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--strategy",
    type=click.Choice(["force", "merge"]),
    default="force",
    show_default=True,
    help="force = wipe DB first; merge = cumulate with existing data.",
)
@click.pass_context
def import_cmd(ctx: click.Context, file: Path, strategy: str) -> None:
    """Load a JSON dump produced by ``brain export``."""
    conn = _conn(ctx.obj["db_path"])
    init_schema(conn)
    report = export_import.read_import(conn, file, strategy=strategy)
    click.echo(f"strategy: {strategy}")
    click.echo(
        f"  nodes: {report['nodes_created']} created, {report['nodes_updated']} updated"
    )
    click.echo(
        f"  rels : {report['rels_created']} created, {report['rels_updated']} updated"
    )


@cli.command()
@click.argument("src")
@click.argument("into_kw")
@click.argument("dst")
@click.pass_context
def merge(ctx: click.Context, src: str, into_kw: str, dst: str) -> None:
    """Merge SRC INTO DST. SRC disappears, DST inherits its edges and sources."""
    if into_kw.lower() != "into":
        raise click.UsageError("syntax: brain.py merge SRC INTO DST")
    conn = _conn(ctx.obj["db_path"])
    result = merge_mod.merge_nodes(conn, src, dst)
    click.echo(
        f"merged {result['src']} into {result['dst']}: "
        f"{result['outgoing_moved']} outgoing + {result['incoming_moved']} incoming edges moved"
    )


if __name__ == "__main__":
    cli()
