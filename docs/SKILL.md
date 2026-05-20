---
name: brain
description: Causal knowledge graph CLI backed by Kuzu. Query existing nodes/edges to answer "why / how / what-relates-to / tradeoff" questions without re-reading source files. Ingestion is automated by the background sync agent — do NOT call brain_ingest yourself unless the user explicitly invokes /ingest.
---

# brAIn — Causal Knowledge Graph Skill

A Kuzu graph used as the user's external causal memory. Two roles share this
graph:

1. **You, in a working session** — QUERY the graph for context (architecture,
   tradeoffs, "why" questions). Do NOT ingest. Graph maintenance is not your
   job.
2. **The background sync agent** (`brain_sync_agent.sh`) — spawned by the Stop
   hook, runs in a separate headless `claude --print` process, extracts
   structural changes from the project diff using the full protocol below
   and ingests them with `brain ingest`. This is the ONLY automated path
   that writes to the graph.

The single exception where a working session ingests: the user explicitly
invokes the `/ingest` slash command, or types "ingest this document". Then
follow the extraction protocol described in this file.

**The system enforces a lot of rules at ingest time** (see "What the code
enforces" below). When you do ingest (user-invoked only): write what you
think is right, run `brain check`, fix what it reports, iterate. Don't try
to remember every rule.

## When to use the QUERY tools

Use `brain_find`, `brain_show`, `brain_causes`, `brain_effects`, `brain_paths`
when the user asks: "why X", "how does X relate to Y", "what alternative was
rejected", "show me the design rationale for Z", "what does this module
prevent / enable in the rest of the system".

Use Read on source files for: exact function signatures, current line numbers
for edits, debug output, post-edit verification, anything that depends on
text exactness.

## When to follow the ingestion protocol below

ONLY when the user has explicitly asked for it. Phrases like "ingest this
document", "/ingest <path>", "add this to the brain". Never spontaneously.

## Vocabulary

### Node types — open vocabulary

Any non-empty string accepted; unknown types are logged but not rejected.
Reuse existing nodes when possible — coherence comes from the `brain_find` /
`brain_check` lookup, not from type enforcement.

| Type        | When to use                                                     |
|-------------|-----------------------------------------------------------------|
| `concept`   | Abstract idea (eventual consistency).                           |
| `entity`    | Named, identifiable thing (PostgreSQL).                         |
| `event`     | Something that happens in time (2024 outage).                   |
| `claim`     | A debatable assertion (cache invalidates too often).            |
| `mechanism` | A process that turns a cause into an effect.                    |
| `algorithm` | A named computational procedure (backpropagation, k-means).     |
| `property`  | A measurable attribute (p99 latency).                           |
| `artifact`  | A produced object (code, document, system, schema).             |
| `process`   | A goal-directed sequence of steps.                              |
| `person`    | A human actor.                                                  |
| `place`     | A location.                                                     |

### Relation types — strict whitelist (code rejects others)

**Causal core (prefer these — the graph's reason to exist):**

| Type          | Meaning                                          |
|---------------|--------------------------------------------------|
| `causes`      | A produces B.                                    |
| `prevents`    | A blocks B from happening.                       |
| `requires`    | B cannot exist without A.                        |
| `enables`     | A makes B possible (without forcing it).         |
| `precedes`    | A happens before B (temporal only, no causation).|
| `contradicts` | A and B are logically incompatible.              |

**Structural:**

| Type          | Meaning                                          |
|---------------|--------------------------------------------------|
| `is_a`        | A is a kind of B.                                |
| `part_of`     | A is a component of B.                           |
| `instance_of` | A is a concrete instance of B.                   |
| `similar_to`  | A resembles B without being an instance.         |
| `property_of` | A is a property of B.                            |

**Fallback (audit warns above 5%):** `related_to`.

## Confidence calibration

| Confidence | Meaning                                              |
|-----------:|------------------------------------------------------|
| `1.0`      | Explicitly stated in the text with a direct verb.    |
| `0.7–0.9`  | Reasonable inference.                                |
| `0.4–0.6`  | Plausible hypothesis not demonstrated by the text.   |
| `< 0.4`    | Don't ingest — better to omit than to pollute.       |

## Extraction workflow

### 1. Read the document in full. No skimming.

### 2. Section inventory.
List every `##`-level heading as your coverage checklist. Every section must
produce ≥ 1 node or an explicit skip note — accidental skips are the main
source of incomplete graphs.

### 3. First pass — nodes.
Section by section, extract every concept the document *covers in depth*.
For each node: pick `type`, write the `label` (plain ASCII — `brain_check`
warns on `()/.+`), write the `description` (one disambiguating sentence,
~30–400 chars — `brain_check` warns on both extremes), set `importance`
(0.5 default, 1.0 for pivotal concepts only).

**Three special cases that must never be missed:**

1. **Marked decisions** (`[ACQUIRED]`, ✓, "decided"): produce a node. The most
   reliable facts in the document.
2. **Rejected alternatives with documented rationale:** create a `claim` node
   for the rejected approach, link it with `contradicts` (or `prevents`) to
   the chosen approach, put the rejection rationale in `evidence`. This is
   causal gold — the graph health metric `tradeoff_ratio` measures whether
   you captured these.
3. **Named sub-components with distinct causal roles:** if a parent concept
   has N named sub-components, create individual nodes **only if each has a
   distinct cause/effect fingerprint**. Otherwise bundle them in the parent's
   description.

### 4. Second pass — relations.
For each connection worth making:
- Prefer a causal type. The graph is judged on its `causal_ratio`.
- Fill `evidence` with the mechanism: "X causes Y because Z". `brain_check`
  warns on evidence < 30 chars.
- Calibrate `confidence`.
- Avoid `related_to` — skip the edge rather than dilute.

### 5. `brain_check <payload>`.
Dry-run validation. Reports missing endpoints, slug pitfalls, lint warnings,
zero-causal payloads, potential duplicates against the existing graph. Fix
and re-run until it says PASS.

### 6. `brain_ingest <payload>`.
Writes. Prints a post-ingest health summary: `causal | structural | tradeoff
| orphans | related_to`. If the summary doesn't look right, you have material
to fix before moving on. Refuses error-level payloads — strict by design.

### 7. Gap verification (cognitive).
Re-read the source with the ingested node list visible:
```bash
brain query "MATCH (n:Node) WHERE '<doc_id>' IN n.sources RETURN n.label, n.type, n.description ORDER BY n.importance DESC"
```
Section by section: is the core content represented as a dedicated node or
clearly captured in some node's description? Each gap is either extracted
(re-ingest is idempotent) or skipped with an explicit reason. Catches
semantic omissions that structural audit cannot.

## Anti-duplicate

Before creating a node with a label close to an existing one, run
`brain_find <fragment>`. The `brain_check` step also reports substring
matches against the existing graph as warnings. When in doubt, **reuse** —
fragmentation is worse than coarseness, and `brain merge` is always available
later.

## What the code enforces (so you don't have to remember)

The validator + checker + auditor now handle these automatically:

- **`project:<name>` tag** is auto-injected on every node and rel based on
  the `doc_id` (must match `project_<name>_<aspect>` or `project_<name>`).
- **Slug pitfalls**: labels with `()`, `/`, `.`, `+` produce a warning at
  check time so you can rename before `id` gets silently rewritten.
- **Description length**: < 30 chars or > 400 chars → lint warning.
- **Evidence length**: < 30 chars → lint warning (single-word evidence rejected).
- **Self-loops**, **invalid rel types**, **importance/confidence out of [0,1]**
  → rejected.
- **Substring duplicate candidates** against the existing graph → reported by
  `brain_check`.
- **Missing endpoint** (a rel references a node id absent from both payload
  and graph) → reported, blocks ingest unless `--force`.
- **Zero causal edges** in a non-trivial payload → blocks ingest unless
  `--force` or `--no-causal-check`.
- **Causal ratio**, **structural dominance**, **tradeoff ratio**, **orphan
  ratio**, **related_to ratio** → printed after every ingest.
- **Re-ingestion of the same `doc_id`** is safe and idempotent — purges
  previous contributions then writes the new ones.

You can override the strict checks with `--force` on the CLI. The MCP
`brain_ingest` has no override — fix the payload.

## Query workflow

| Question shape                              | Command                              |
|---------------------------------------------|--------------------------------------|
| "Why did X happen?" / "What causes X?"      | `brain causes X`                     |
| "What does Y lead to?"                      | `brain effects Y`                    |
| "How does A relate to B?"                   | `brain paths A B`                    |
| "Show me node X"                            | `brain show X`                       |
| "Find nodes about <topic>"                  | `brain find <topic>`                 |
| Advanced traversal                          | `brain query "<cypher>"`             |

When answering, **cite the `evidence`** printed by the CLI. If a chain
contains edges with `confidence < 0.6`, flag that explicitly — don't smuggle
weak inferences into a confident-sounding reply.

## Worked example

Source paragraph:

> The Redis cache was misconfigured with a TTL of 5 seconds, while the median
> time between two requests on the hot keys was around 30 seconds. As a
> result, almost every read missed the cache and fell back to the database,
> driving p99 latency past 800ms.

Extraction:

```json
{
  "doc_id": "redis_postmortem_2026_q1",
  "nodes": [
    {"label": "TTL 5 seconds", "type": "claim",
     "description": "Redis TTL configured at 5 seconds for hot keys — shorter than the mean inter-request interval, guarantees expiry between accesses."},
    {"label": "Request interval 30s", "type": "property",
     "description": "Median time between two requests on the same hot key — six times the configured TTL."},
    {"label": "Cache miss storm", "type": "event",
     "description": "Near-total cache miss rate on hot keys following the TTL/interval mismatch."},
    {"label": "Database fallback", "type": "event",
     "description": "Read traffic flowing to PostgreSQL when Redis misses — direct cache miss consequence."},
    {"label": "p99 latency 800ms", "type": "property",
     "description": "Observed 99th-percentile request latency during the incident, ~10x the normal cached path."}
  ],
  "rels": [
    {"src": "ttl_5_seconds", "dst": "cache_miss_storm", "type": "causes", "confidence": 0.95,
     "evidence": "TTL shorter than mean inter-request time guarantees expiry between accesses — the entry is gone every time the next read comes."},
    {"src": "request_interval_30s", "dst": "cache_miss_storm", "type": "enables", "confidence": 0.9,
     "evidence": "Slow request rate amplifies the effect of a short TTL — a faster rate would have absorbed expiries."},
    {"src": "cache_miss_storm", "dst": "database_fallback", "type": "causes", "confidence": 1.0,
     "evidence": "Cache miss => the read goes to the database by definition of the cache-aside pattern."},
    {"src": "database_fallback", "dst": "p99_latency_800ms", "type": "causes", "confidence": 0.9,
     "evidence": "Direct database reads are ~10x slower than the cache layer here — explains the observed latency rise."}
  ]
}
```

Sample queries afterwards:

```bash
brain causes p99_latency_800ms   # walks back through database_fallback → cache_miss_storm → ttl_5_seconds
brain effects ttl_5_seconds      # walks forward to the latency outcome
brain paths ttl_5_seconds p99_latency_800ms
```
