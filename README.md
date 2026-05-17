# brAIn

**A causal knowledge graph built for [Claude](https://claude.ai/code) — store structure, not text.**

brAIn is a persistent external memory for Claude. Instead of re-reading documents every session, Claude extracts their causal structure once and stores it as a graph of atomic claims linked by typed edges (`causes`, `prevents`, `requires`, `enables`). Future sessions query the graph directly — no re-reading, no re-summarizing, no lost context.

It is designed to be used with [Claude Code](https://claude.ai/code) as a skill or MCP server. Claude performs all semantic extraction; `brain.py` handles persistence, deduplication, and queries.

![brAIn graph explorer](docs/screenshot_graph.png)

## How it works

You point Claude at a document. Claude reads it, extracts nodes and causal relations, and emits a structured JSON payload. `brain.py` ingests it into a local [Kuzu](https://kuzudb.com) graph database. In any future session, Claude can query the graph to answer *why*, *what if*, and *how does A lead to B* — without ever touching the original document again.

| Approach | What's stored | What's retrieved |
|---|---|---|
| RAG / vector DB | Chunks of source text | Paragraphs that match the query |
| brAIn | Causal structure extracted from text | Nodes + evidence chains |

**brAIn contains zero LLM calls.** There are no AI libraries, no NLP parsers, nothing inside `lib/`. The intelligence lives entirely in Claude. `brain.py` is pure plumbing — validation, deduplication, Cypher queries, persistence.

The graph is the asset. The code is replaceable.

## Features

- **Claude Code integration** — skill manifest (`docs/SKILL.md`) and MCP server (`mcp_server.py`) with 8 graph tools available in every session
- **CLI** (`brain.py`) — init, ingest, find, show, causes, effects, paths, query, stats, audit, export, import, merge
- **React UI** — interactive graph explorer, search + type filters, node detail panel, one-click project ingestion via a dedicated Claude session
- **Kuzu embedded graph DB** — Cypher queries, no server, no cloud, local storage in `graph/kuzu_db/`
- **Idempotent re-ingestion** — re-running `ingest` on an existing `doc_id` replaces its contributions cleanly without touching other documents' edges
- **Typed vocabulary** — strict relation whitelist, open node types

## Quick start

**Requirements:** Python ≥ 3.10, Node 18+ (UI only).

```bash
git clone git@github.com:SilenceKatharos/brAIn.git
cd brAIn
python3 -m venv .venv
.venv/bin/pip install kuzu click

.venv/bin/python brain.py init
.venv/bin/python brain.py ingest examples/sample.json

.venv/bin/python brain.py stats
.venv/bin/python brain.py effects bus_factor_of_one
.venv/bin/python brain.py paths bus_factor_of_one project_death
```

**Optional: global `brain` command.** Instead of `.venv/bin/python brain.py` every time:

```bash
cat > ~/.local/bin/brain << 'EOF'
#!/usr/bin/env bash
exec /absolute/path/to/brAIn/.venv/bin/python /absolute/path/to/brAIn/brain.py "$@"
EOF
chmod +x ~/.local/bin/brain

brain stats
brain find redis
brain effects cache_miss_storm
```

## Using with Claude Code

### As an MCP server (recommended)

Registers 8 graph tools in **every** Claude Code session, regardless of working directory.

```bash
realpath .   # run from inside the brAIn directory to get the absolute path
```

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "brain": {
      "command": "/absolute/path/to/brAIn/.venv/bin/python",
      "args": ["/absolute/path/to/brAIn/mcp_server.py"]
    }
  }
}
```

Available tools: `brain_find`, `brain_show`, `brain_causes`, `brain_effects`, `brain_paths`, `brain_query`, `brain_stats`, `brain_ingest`.

### As a skill

Add to `.claude/CLAUDE.md` in any project:

```
@/absolute/path/to/brAIn/docs/SKILL.md
```

Claude will follow the full extraction protocol: section inventory → entity pass → relation pass → completeness review → gap verification.

## React UI

```bash
# Backend (from the brAIn root)
.venv/bin/pip install fastapi "uvicorn[standard]"
.venv/bin/python ui/backend/main.py      # → http://localhost:8000

# Frontend (separate terminal)
cd ui/frontend
npm install
npm run dev                              # → http://localhost:5173
```

The UI includes an **Ingest panel** — enter a project name and folder path, and the backend spawns a dedicated Claude session that autonomously reads, extracts, and ingests the project's markdown files.

![brAIn ingest panel](docs/screenshot_ingest.png)

## CLI reference

```
brain.py init                    Create the database and schema (idempotent)
brain.py ingest <file.json>      Ingest a payload; safe to re-run
brain.py find <pattern>          Search nodes by label or id substring
brain.py show <node_id>          Print a node + its incoming/outgoing edges
brain.py causes <node>           Walk upstream causal chain
brain.py effects <node>          Walk downstream causal chain
brain.py paths <src> <dst>       Find paths up to 4 hops between two nodes
brain.py query "<cypher>"        Run raw Cypher
brain.py stats                   Node/relation counts by type
brain.py audit                   Health report (orphans, related_to ratio, top-degree nodes)
brain.py export <file>           Dump full graph to JSON
brain.py import <file>           Load a dump (--strategy force | merge)
brain.py merge SRC INTO DST      Merge node SRC into DST; SRC is deleted
brain.py context <topic>         Get node + neighborhood for a topic (used by MCP)
```

## Ingestion format

```json
{
  "doc_id": "redis_postmortem_2026_q1",
  "nodes": [
    {
      "id": "ttl_too_short",
      "label": "TTL too short",
      "type": "claim",
      "description": "Redis TTL set to 5s for hot keys whose median inter-request interval is 30s.",
      "importance": 0.85
    },
    {
      "id": "cache_miss_storm",
      "label": "Cache miss storm",
      "type": "event",
      "description": "Near-total cache miss rate on hot keys, all reads falling back to the database."
    }
  ],
  "rels": [
    {
      "src": "ttl_too_short",
      "dst": "cache_miss_storm",
      "type": "causes",
      "confidence": 0.95,
      "evidence": "TTL shorter than the mean inter-request interval guarantees expiry between every two accesses."
    }
  ]
}
```

**ID rules:** `id` is canonicalized to `slugify(label)` — lowercase, non-alphanumeric → `_`. Avoid parentheses, slashes, and dots in labels to prevent silent ID rewrites that break relation lookups.

**Confidence calibration:**

| Value | Meaning |
|---|---|
| `1.0` | Explicitly stated with a direct causal verb |
| `0.7–0.9` | Reasonable inference from the text |
| `0.4–0.6` | Plausible hypothesis, not demonstrated |
| `< 0.4` | Don't ingest — omit rather than pollute |

## Vocabulary

### Relation types (strict whitelist)

| Type | Meaning |
|---|---|
| `causes` | A produces B |
| `prevents` | A blocks B |
| `requires` | B cannot exist without A |
| `enables` | A makes B possible without forcing it |
| `precedes` | A happens before B (temporal only) |
| `contradicts` | A and B are logically incompatible |
| `is_a` | A is a kind of B |
| `part_of` | A is a component of B |
| `instance_of` | A is a concrete instance of B |
| `similar_to` | A resembles B without being an instance |
| `property_of` | A is a property of B |
| `related_to` | Unqualified link — avoid, keep under 5% of edges |

Any type outside this list is rejected at ingestion and logged to `extension_requests.jsonl`.

### Node types (open vocabulary)

Any non-empty string is accepted. Unknown types are logged to `extension_requests.jsonl` for review. Common types: `concept`, `entity`, `event`, `claim`, `mechanism`, `algorithm`, `property`, `person`, `artifact`, `process`.

## Design principles

1. **The graph is the asset, not the code.** The code stays simple and replaceable; the graph compounds in value over time.
2. **One assertion beats one paragraph.** `ttl_too_short –causes→ cache_miss_storm` with a one-sentence `evidence` is more useful than three paragraphs to re-read.
3. **Causality first.** Taxonomic relations (`is_a`, `part_of`) are support structure. The core is `causes / prevents / requires / enables`.
4. **Source documents are disposable.** If extraction was correct, you should be able to delete the source and keep reasoning. If you can't, the extraction was incomplete.
5. **Traceability by default.** Every node and edge carries `sources` (origin doc_ids) and `evidence` (the reasoning). You can always trace why something is in the graph.
6. **On-demand retrieval, not preemptive injection.** Claude never loads the whole graph into context — it fetches only what it needs, when it needs it, via tool calls.

## Testing

```bash
.venv/bin/pytest tests/ -q
.venv/bin/python -m coverage run --source=lib,brain -m pytest tests/ -q
.venv/bin/python -m coverage report
```

## Project layout

```
brAIn/
├── brain.py              # CLI entrypoint
├── mcp_server.py         # MCP server for Claude Code integration
├── lib/                  # Core modules (db, ingest, query, audit)
├── docs/
│   ├── SKILL.md          # Claude extraction protocol
│   └── SCHEMA.md         # Graph schema reference
├── ui/
│   ├── backend/          # FastAPI backend
│   └── frontend/         # React + Vite frontend
├── examples/
│   └── sample.json       # Worked example (open-source project mortality)
├── projects/             # Extracted project payloads
└── experiments/          # Research scripts (corpus download, extraction comparison)
```

## License

MIT — see [`LICENSE`](LICENSE).
