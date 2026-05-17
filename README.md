# brAIn

**A causal knowledge graph you build by reading, not by indexing.**

Most AI memory systems are libraries of text: the model reads files, chunks them, and retrieves paragraphs. brAIn does the opposite — it stores the *causal structure* that documents describe, not the documents themselves.

When you extract a document into brAIn, the source becomes disposable. What remains is a graph of atomic claims, linked by edges like `causes`, `prevents`, `requires`, `enables`. You can then ask *why*, *what if*, and *how does A lead to B* — and get precise, traceable answers from the graph.

![brAIn graph explorer](docs/screenshot_graph.png)

---

## What makes it different

| Approach | What's stored | What's retrieved |
|---|---|---|
| RAG / vector DB | Chunks of source text | Paragraphs that match the query |
| brAIn | Causal structure extracted from text | Nodes + evidence chains |

**brAIn does no extraction itself.** There are no LLM calls, no NLP parsers, nothing inside `lib/`. The extraction is performed by Claude at conversation time (as a Claude Code skill or via the UI), which emits a structured JSON payload that `brain.py` validates, deduplicates, and persists.

The graph is the asset. The code is just plumbing.

---

## Features

- **CLI** (`brain.py`) — init, ingest, find, show, causes, effects, paths, query, stats, audit, export, import, merge
- **React UI** — interactive graph explorer, search + type filters, node detail panel, one-click project ingestion
- **MCP server** — exposes graph tools (`brain_find`, `brain_show`, `brain_causes`, `brain_effects`, `brain_paths`, `brain_query`, `brain_ingest`, `brain_stats`) directly to Claude Code in any session
- **Kuzu embedded graph DB** — Cypher queries, no server, no cloud, local storage in `graph/kuzu_db/`
- **Idempotent re-ingestion** — re-running `ingest` on an existing `doc_id` replaces its contributions cleanly without touching other documents' edges
- **Typed vocabulary** — strict relation whitelist (`causes`, `prevents`, `requires`, `enables`, `precedes`, `contradicts`, + structural types), open node types

---

## Quick start

**Requirements:** Python ≥ 3.10, Node 18+ (UI only).

```bash
git clone https://github.com/your-username/brAIn
cd brAIn
python3 -m venv .venv
.venv/bin/pip install kuzu click          # core deps only

# Initialize the graph
.venv/bin/python brain.py init

# Ingest the bundled sample (open-source project mortality — 18 nodes, 26 rels)
.venv/bin/python brain.py ingest examples/sample.json

# Explore
.venv/bin/python brain.py stats
.venv/bin/python brain.py effects bus_factor_of_one
.venv/bin/python brain.py paths bus_factor_of_one project_death
.venv/bin/python brain.py audit
```

**Optional: global `brain` command.** Instead of `.venv/bin/python brain.py` every time, create a wrapper:

```bash
cat > ~/.local/bin/brain << 'EOF'
#!/usr/bin/env bash
exec /absolute/path/to/brAIn/.venv/bin/python /absolute/path/to/brAIn/brain.py "$@"
EOF
chmod +x ~/.local/bin/brain

# Then use anywhere:
brain stats
brain find redis
brain effects cache_miss_storm
```

---

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

---

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

**ID rules:** node `id` is always canonicalized to `slugify(label)` — lowercase, non-alphanumeric → `_`. Avoid parentheses, slashes, and dots in labels to prevent silent ID rewrites that break relation lookups.

**Confidence calibration:**

| Value | Meaning |
|---|---|
| `1.0` | Explicitly stated with a direct causal verb |
| `0.7–0.9` | Reasonable inference from the text |
| `0.4–0.6` | Plausible hypothesis, not demonstrated |
| `< 0.4` | Don't ingest — omit rather than pollute |

---

## Using with Claude Code

### As a skill

Create `.claude/CLAUDE.md` at the root of any project and add:

```
@/absolute/path/to/brAIn/docs/SKILL.md
```

Claude will then know how to extract documents (section inventory → entity pass → relation pass → gap verification), query the graph causally, and check for existing nodes before creating new ones.

### As an MCP server (recommended)

The MCP server makes graph tools available in **every** Claude Code session, regardless of working directory — no per-project setup needed.

```bash
# Find your brAIn path
realpath .   # run from inside the brAIn directory
```

Then add to `~/.claude/settings.json`:

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

---

## React UI

The UI lives in `ui/`. Start the backend first, then the frontend.

```bash
# Backend (from the brAIn root)
.venv/bin/pip install fastapi "uvicorn[standard]"
.venv/bin/python ui/backend/main.py      # → http://localhost:8000

# Frontend (in a separate terminal)
cd ui/frontend
npm install
npm run dev                              # → http://localhost:5173
```

**UI features:**
- Graph canvas with force-directed layout, importance-scaled nodes, color-coded by type
- Sidebar with live search + type filter badges
- Node detail panel showing description, relations, evidence, and sources
- **Ingest panel** — enter a project name and folder path; the backend spawns a dedicated Claude session that reads the project's markdown files and ingests them automatically

![brAIn ingest panel](docs/screenshot_ingest.png)

---

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

Node types are a reference vocabulary, not a hard constraint. Any non-empty string is accepted. Unknown types are logged to `extension_requests.jsonl` for review. Common types: `concept`, `entity`, `event`, `claim`, `mechanism`, `algorithm`, `property`, `person`, `artifact`, `process`.

---

## Design principles

1. **The graph is the asset, not the code.** The code stays simple and replaceable; the graph compounds in value over time.
2. **One assertion beats one paragraph.** `ttl_too_short –causes→ cache_miss_storm` with a one-sentence `evidence` is more useful than three paragraphs to re-read.
3. **Causality first.** Taxonomic relations (`is_a`, `part_of`) are support structure. The core is `causes / prevents / requires / enables`.
4. **Source documents are disposable.** If extraction was correct, you should be able to delete the source and keep reasoning. If you can't, the extraction was incomplete.
5. **Traceability by default.** Every node and edge carries `sources` (origin doc_ids) and `evidence` (the reasoning). You can always trace why something is in the graph.
6. **On-demand retrieval, not preemptive injection.** Claude never loads the whole graph into context — it fetches only what it needs, when it needs it, via tool calls.

---

## Testing

```bash
.venv/bin/pytest tests/ -q
.venv/bin/python -m coverage run --source=lib,brain -m pytest tests/ -q
.venv/bin/python -m coverage report
```

---

## Project layout

```
brAIn/
├── brain.py              # CLI entrypoint
├── mcp_server.py         # MCP server for Claude Code integration
├── lib/                  # Core modules (db, ingest, query, audit)
├── docs/
│   ├── SKILL.md          # Claude extraction skill manifest
│   └── SCHEMA.md         # Graph schema reference
├── ui/
│   ├── backend/          # FastAPI backend
│   └── frontend/         # React + Vite frontend
├── examples/
│   └── sample.json       # Worked example (open-source project mortality)
├── graph/kuzu_db/        # Persistent graph (created on first init)
├── projects/             # Extracted project payloads
└── experiments/          # Research scripts (corpus download, extraction comparison)
```

---

## License

MIT — see [`LICENSE`](LICENSE).
