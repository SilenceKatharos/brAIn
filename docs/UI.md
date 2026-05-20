# React UI

A local browser interface to explore the graph, search, inspect node
evidence, and trigger autonomous project ingestion through a dedicated
Claude session.

![graph explorer](screenshot_graph.png)

## Run

```bash
# In one terminal — FastAPI backend
.venv/bin/uvicorn ui.backend.main:app --port 8000

# In another — Vite dev server with the /api proxy
cd ui/frontend && npm run dev
# → http://localhost:5173

# OR, if you already built the frontend:
cd ui/frontend && npm run preview -- --port 4173
# → http://localhost:4173
```

`ui/start.sh` launches both at once with cleanup on Ctrl-C.

## Layout

Three-column SPA:

| Column | Content |
|---|---|
| **Left** | Search box · type-filter chips · node list (sorted by importance) · importance slider |
| **Center** | ReactFlow canvas with d3-force layout · MiniMap · zoom controls |
| **Right** | Selected-node detail panel: type, importance, sources, all edges with their evidence |

### Type filter chips

One chip per node type present in the graph, color-coded:

| Type | Color |
|---|---|
| `concept` | blue |
| `entity` | green |
| `algorithm` | purple |
| `artifact` | orange |
| `mechanism` | red |
| `property` | pink |
| `event` | grey |
| `claim` | amber |
| `process` | emerald |
| `person` | cyan |
| `place` | lime |

Same palette drives the MiniMap and the node circles.

### Importance threshold visibility

The slider on the left controls which nodes render: `importance >=
threshold`. Starts at 0.7 by default. Combined with the `expandedNodeIds`
set (double-click a node to reveal its hidden neighbors), this gives the
"start zoomed out, drill into dense regions" navigation pattern.

### Hidden neighbor badge

When a node has neighbors below the threshold, its circle shows a `+N`
badge. Double-click expands those neighbors into view.

### Default view

On first load, the canvas opens with the most important node of each
tracked project (detected via the `project:*` source tag). Search and
type filters override this and show their full result sets.

## Backend endpoints

| Endpoint | Method | Returns |
|---|---|---|
| `/api/graph` | GET | Full graph (all nodes + edges with descriptions, sources, evidence) |
| `/api/node/{id}` | GET | One node + 20-neighbor context (description, type, importance, all edges) |
| `/api/search?q=&type=&limit=` | GET | Substring search on label/description, filterable by type |
| `/api/stats` | GET | Node/rel counts by type + totals |
| `/api/audit` | GET | Full health report from `lib.audit.run_audit` |
| `/api/types` | GET | List of distinct node types in the graph |
| `/api/ingest` | POST | Start an autonomous ingestion job for a project directory |
| `/api/ingest/{job_id}/status` | GET | Poll ingestion job status |

The backend opens its own Kuzu connection (`read_only=True` by default;
the ingest endpoint reopens with write access). Same `lib/` engine
used by the CLI and MCP.

## Autonomous ingest panel

![ingest panel](screenshot_ingest.png)

Click **Ingest project** in the header. The modal asks for:
- **Project name** (becomes the `<name>` in `project:<name>` tag)
- **Folder path** (absolute path to the project directory)

On submit, the backend spawns a **dedicated Claude session** as a child
process. The session:
1. Reads the project's `*.md`, `*.py`, `*.jsx`, `*.tsx`, `*.js`, `*.sh`
   files
2. Follows the extraction protocol from `docs/SKILL.md`
3. Emits a JSON payload
4. Runs `brain ingest` to persist

For code files, the protocol extracts **architectural decisions** —
why a module exists, what it prevents or enables, rejected alternatives
— not line-by-line logic.

The job streams output to the UI in real time (polled every 2 seconds
via `/api/ingest/{job_id}/status`). On completion, the graph reloads
and the new doc's nodes are auto-expanded.

> **Note:** the ingest panel is the **autonomous** ingestion path,
> mostly equivalent to the background sync agent but triggered from the
> UI and run synchronously on the user-specified folder. The Claude
> Code sync agent is the recommended path for active development; the
> UI ingest is useful for bootstrapping an existing project.

## Cost note

The ingest panel uses one full `claude --print`-equivalent session per
ingestion, which may scan dozens of files in one shot. Expect $0.10–$1
per project depending on size. Subsequent updates should use the sync
agent (incremental, per-turn delta) rather than re-running the panel.

## Implementation notes

- **Layout**: synchronous d3-force simulation (400 ticks). Blocks the
  main thread for ~500 ms on a 300-node graph. Acceptable for
  development; a Web Worker version exists in branch sketches but
  isn't merged yet.
- **Positions persisted** across expansions via `positionsRef` so
  existing nodes don't jump when new ones appear.
- **CORS** allows all origins (development convenience). The backend
  is intended to be local-only.
- **Tests**: none yet for the UI — coverage is limited to `lib/` and
  `brain.py`.
