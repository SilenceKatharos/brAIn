# CLI reference

Once `install.sh` has wired the global `brain` shim, any command can be
run from any directory. The full command set:

| Command | Effect |
|---|---|
| `brain init` | Create the Kuzu DB and schema (idempotent) |
| `brain ingest <file>` | Ingest a JSON payload; runs `check` first, refuses on errors unless `--force` |
| `brain check <file>` | Dry-run validation: schema, slugs, endpoints, causal balance, duplicates |
| `brain find <pattern>` | Search nodes by id or label substring |
| `brain show <node_id>` | Print node + incoming/outgoing edges with evidence |
| `brain causes <node_id>` | Walk upstream (causes / prevents / requires / enables / precedes) |
| `brain effects <node_id>` | Walk downstream (causes / enables / precedes) |
| `brain paths <src> <dst>` | Variable-length paths between two nodes (default max 4 hops) |
| `brain context <topic>` | Topic + 1-hop neighborhood as JSON (used by MCP) |
| `brain query "<cypher>"` | Raw Cypher escape hatch — JSON output |
| `brain stats` | Counts by node/rel type + totals |
| `brain audit` | Full health report: volumes, ratios, top-degree, contributions by doc |
| `brain export <file>` | Dump the whole graph to JSON |
| `brain import <file>` | Load a dump, `--strategy {force, merge}` |
| `brain merge SRC into DST` | Collapse two nodes — SRC disappears, DST inherits its edges |

## Flags worth knowing

### `brain ingest --force`

The CLI accepts `--force` to override `check` errors (missing endpoints,
zero causal edges, rejected nodes/rels). The MCP path **has no
equivalent** — see [Architecture](ARCHITECTURE.md#two-trust-models-cli-vs-mcp).

### `brain ingest --no-causal-check`

Skips the precondition that "a payload with ≥ 5 rels must contain at
least one `causes` / `prevents` / `contradicts` edge". Used by the
background sync agent because incremental syncs are often purely
structural (a new constant, a new flag).

### `brain causes / effects --depth N`

Walks N levels deep. Default 3. Higher values can produce large output
on dense subgraphs; use sparingly.

### `brain paths --max-hops N --limit L`

`max-hops` is the longest chain considered (default 4). `limit` is the
maximum number of paths returned (default 10).

### `brain audit --json`

Emits the full audit as JSON instead of human-readable. Useful for
scripts that gate CI on health metrics.

### `brain import --strategy {force, merge}`

`force` wipes the DB before loading the dump. `merge` cumulates with
existing data using the same upsert logic as `ingest`.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Clean — no warnings |
| `1` | Lint warnings (data was written) — long/short descriptions, weak evidence, rewritten ids |
| `2` | Real failure — rejected nodes/rels, missing endpoints, data loss in this run |

The sync agent treats `exit <= 1` as success (data landed), `exit == 2`
as a real failure that needs review.

## Examples

```bash
# Bootstrap a fresh install
brain init
brain stats               # 0 nodes, 0 rels

# Ingest the bundled sample
brain ingest examples/sample.json
brain stats               # 18 nodes, 26 rels

# Lookup
brain find death          # nodes containing "death" in id/label
brain show project_death  # full detail with evidence on every edge

# Walk causal chains
brain causes project_death
brain effects bus_factor_of_one
brain paths bus_factor_of_one project_death

# Audit the graph
brain audit               # ratios, top-degree, contributions

# Dry-run a new payload before ingest
brain check projects/myproject/myproject.json

# Force-ingest despite warnings (CLI only)
brain ingest projects/myproject/myproject.json --force

# Merge two nodes that should be one
brain merge legacy_name into new_name

# Backup / restore
brain export /tmp/brain_backup.json
brain import /tmp/brain_backup.json --strategy merge

# Raw Cypher for advanced filtering
brain query "MATCH (n:Node) WHERE 'project:lumora' IN n.sources RETURN n.label, n.type"
```
