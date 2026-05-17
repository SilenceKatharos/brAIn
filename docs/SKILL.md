---
name: brain
description: Causal knowledge graph CLI backed by Kuzu. Ingest documents by extracting nodes and causal relations into a persistent graph, then query the graph instead of rereading the sources. Use when the user asks to "add to the brain", "ingest into the graph", "what causes X", "what does Y lead to", or "how does A relate to B" against an existing knowledge graph.
---

# brAIn — Causal Knowledge Graph Skill

This skill lets you treat a Kuzu graph as the user's external causal memory.
You are responsible for the **semantic extraction**: reading a document, picking
out atomic claims, and emitting structured JSON. The `brain.py` CLI does the
plumbing — validation, deduplication, persistence, querying.

## When to invoke

Trigger phrases (in any language):
- "ingest this into the brain", "add this document to the graph"
- "what does the brain say about X", "ask the brain why Y"
- "what causes X according to my notes", "what does Y lead to"
- "how does A relate to B in the brain", "find a path from A to B"
- "audit the graph", "show stats of the brain"

Don't invoke for:
- Linear note-taking — that belongs in Obsidian or a markdown file.
- Storing text verbatim — this graph stores *structure*, not paragraphs.
- Storing user preferences — use the auto-memory system for those.

## Project layout

```
brAIn/
├── brain.py              # CLI entrypoint
├── lib/                  # backing modules
├── graph/kuzu_db/        # the persistent graph (created on first init)
├── examples/sample.json  # reference extraction
└── extension_requests.jsonl, potential_duplicates.jsonl  # generated logs
```

Run the CLI with `./.venv/bin/python brain.py <command>` from the project root,
or use whatever Python environment has `kuzu` and `click` installed.

## Vocabulary

### Node types — open vocabulary

Node types are a **reference vocabulary**, not a hard constraint. Any
non-empty string type is accepted at ingest time. If the type is not in the
reference list below, it is logged in `extension_requests.jsonl` for human
review but the node is still created.

Coherence comes from the lookup-before-create protocol (`query_graph`), not
from type enforcement. If a similar node already exists, reuse it — the type
follows from the existing node, not from your classification.

### Node types (reference)

| Type | When to use |
|------|-------------|
| `concept` | Abstract idea (e.g., "eventual consistency"). |
| `entity` | Named, identifiable thing (e.g., "PostgreSQL"). |
| `event` | Something that happens in time (e.g., "2024 outage"). |
| `claim` | A debatable assertion (e.g., "cache invalidates too often"). |
| `mechanism` | A process that turns a cause into an effect. |
| `algorithm` | A named computational procedure (e.g., "backpropagation", "k-means"). |
| `property` | A measurable or qualifiable attribute (e.g., "p99 latency"). |
| `person` | A human actor. |
| `place` | A location. |
| `artifact` | A produced object (code, document, system, schema). |
| `process` | A goal-directed sequence of steps. |

### Relation types — strict whitelist

Relation types **are** strictly enforced. Any type outside this list is
rejected and logged. The causal vocabulary must not drift — it is what makes
the graph traversable and meaningful.

**Causal core (prefer these):**
| Type | Meaning |
|------|---------|
| `causes` | A produces B (factual or statistical). |
| `prevents` | A blocks B from happening. |
| `requires` | B cannot exist without A. |
| `enables` | A makes B possible (without forcing it). |
| `precedes` | A happens before B (temporal only, no causation). |
| `contradicts` | A and B are logically incompatible. |

**Structural support:**
| Type | Meaning |
|------|---------|
| `is_a` | A is a kind of B. |
| `part_of` | A is a component of B. |
| `instance_of` | A is a concrete instance of B. |
| `similar_to` | A resembles B without being an instance. |
| `property_of` | A is a property of B. |

**Fallback (avoid):**
| Type | Meaning |
|------|---------|
| `related_to` | Unqualified link. Sign of lazy extraction — must stay under 5% of total edges. |

## Confidence calibration

| Confidence | Meaning |
|-----------:|---------|
| `1.0` | Explicitly stated in the text with a direct causal verb. |
| `0.7–0.9` | Reasonable inference. |
| `0.4–0.6` | Plausible hypothesis not demonstrated by the text. |
| `< 0.4` | Don't ingest — better to omit than to pollute. |

## Agentic extraction — the query_graph tool

When used via the API in agentic mode, you have access to a `query_graph`
tool. The graph is long-term memory; your context window is working memory.
You never load the whole graph — you retrieve only what you need, when you
need it.

**Protocol — follow this for every extraction:**

1. As you identify each concept in the document, call `query_graph(topic)`
   before deciding to create a node.
2. Read the returned neighborhood. If a node with equivalent semantics exists,
   **reuse its `id` exactly** — do not mint a new node.
3. If no match is found (or `match_count` is 0), create the node with a new id.
4. Once all nodes and relations are decided, emit the final JSON in one block.

**Tool response shape:**

```json
{
  "topic": "neural network",
  "match_count": 1,
  "matches": [
    {
      "id": "neural_network",
      "label": "Neural network",
      "type": "concept",
      "description": "Composite parametric function trained by gradient descent.",
      "importance": 0.85,
      "outgoing": [
        {"rel_type": "enables", "confidence": 0.9, "dst": "backpropagation",
         "dst_label": "Backpropagation", "dst_type": "algorithm"}
      ],
      "incoming": [
        {"rel_type": "is_a", "confidence": 1.0, "src": "perceptron",
         "src_label": "Perceptron", "src_type": "concept"}
      ]
    }
  ]
}
```

**Decision rule:**

- **Reuse the id** if the existing label/description matches your candidate
  (modulo synonymy, plural/singular, abbreviations). Example: "neural net"
  → reuse `neural_network`.
- **Create a new id** if the existing node and your candidate are related but
  genuinely distinct. Example: `backpropagation` and `gradient_descent` are
  distinct — link them, don't collapse them.
- **When in doubt, reuse.** A merge via `brain.py merge` is cheaper than a
  fragmented graph.

**What the context does not dictate:**

Do not blindly create relations to every neighbor shown in the tool response.
Only add a relation if the *document being processed* actually supports it.

## Ingestion workflow

When the user asks you to add a document:

**Step 1 — Read the document in full.** No skimming.

**Step 2 — Structure inventory.** Before extracting anything, list every
`##`-level heading (or equivalent structural unit). This is your extraction
checklist. No section should be accidentally skipped — if a section produces
zero nodes, that must be a conscious decision, not an oversight.

**Step 3 — First pass: entities.** Work section by section through your
checklist. For each section, extract every concept, mechanism, decision,
property, and architectural choice the document *covers in depth*. Be generous
at this stage — do not apply the relation filter yet (that comes in Step 5).

For each node:
- Pick `type` from the reference vocabulary (or any meaningful string if none fits).
- Build a stable `id` from the label (snake_case, ASCII, max 80 chars).
- Write a **comprehensive `description`**: how the concept works, its mechanism,
  quantitative properties, key variants. A reader with only the graph should
  lose no useful information. The source document must be disposable after
  ingestion.
- Set `importance` between 0 and 1 (0.5 default, 1.0 only for pivotal concepts).

**Node creation rule — applied during pass 1:**
> Create a node if this document says enough about the concept to write a real
> description. A concept merely *mentioned in passing* (named but not explained)
> does not get a node — wait for a document that actually covers it.
>
> Do NOT apply a relation test at this stage. A node without relations yet is
> fine; you will find or add its relations in Step 4, and review orphans in Step 5.

**Three special cases that must never be missed:**

1. **Marked decisions (`[ACQUIRED]`, ✓, "decided", etc.):** Any item the
   document explicitly marks as decided or validated must produce a node. These
   are the most reliable facts in the document — skip none.

2. **Rejected alternatives with documented rationale:** When the document
   explains why an alternative was *not* chosen, extract it. The reason for
   rejection is causal gold. Pattern: create a node for the rejected approach
   (type `claim`), link it with `contradicts` or `prevents` to the chosen
   approach, and put the full rejection rationale in `evidence`. Example:
   *"Dollar-pegged PoUW reward"* `contradicts` *"Immutable core"* because
   *"pegging requires a price oracle, which breaks the no-admin-key guarantee."*

3. **Named sub-components with distinct causal roles:** If a concept has
   N explicitly named sub-components (e.g., "7 components of R: R_C, R_V,
   R_F…"), create individual nodes for each sub-component **if they have
   distinct cause/effect relationships to other concepts**. If they are merely
   parallel inputs to the same parent mechanism with no individual causal
   fingerprint, bundle them in the parent node's description instead.

The CLI will canonicalize `id = slugify(label)` automatically; if your `id`
differs, it gets rewritten and logged. To avoid rewrites, **keep your `label`
short enough that its slug matches your intended `id`**.

**Slugification pitfall**: special characters in labels are replaced by underscores.
`"Reliability sigmoid G(f,d)"` → id `reliability_sigmoid_g_f_d`.
`"H/C ratio v0.5"` → id `h_c_ratio_v0_5`.
Any `rel` referencing the pre-rewrite id will be silently skipped.
Rule: **use plain ASCII labels without parentheses, slashes, or dots** when
writing payloads manually. Use `brain find <label>` after ingest to confirm
the actual id before writing rels.

**Step 4 — Second pass: relations.** For each pair worth connecting:
- Prefer a causal type (`causes`, `prevents`, `requires`, `enables`).
- Fill `evidence` with a **full explanation**: the mechanism, the condition, the
  reasoning — not just a label. "X causes Y because Z" is the target. A single
  vague word is not acceptable.
- Calibrate `confidence` per the table above.
- Avoid `related_to` — if you can't qualify the link, skip it.

**Step 5 — Completeness + orphan review.**

*Coverage check:* go back to your section checklist from Step 2. Every section
should have ≥ 1 node. If a section has 0 nodes, re-read it and decide
explicitly: extract something, or write a one-line note to yourself explaining
the skip. Accidental skips are the main source of incomplete graphs.

*Orphan check:* any node with 0 relations after Step 4 is suspect. Either find
a meaningful relation to add (there almost always is one), or drop the node. A
node with no edges contributes nothing to graph traversal.

**Step 6 — Anti-duplicate check.**

*In agentic API mode:* use the `query_graph` tool before each node creation
(see "Agentic extraction" section above).

*In interactive Claude Code mode:*

```bash
./brain.py find <label or fragment>
```

If a close match already exists, reuse its `id` or accept that the ingest
will log a potential-duplicate hint and let the user merge later via
`brain.py merge`.

**Step 5 — Emit the JSON payload.**

```json
{
  "doc_id": "stable_slug_of_doc",
  "nodes": [{...}, ...],
  "rels":  [{...}, ...]
}
```

Save it (e.g., to `examples/<doc>.json`) and run:

```bash
./brain.py ingest <path-to-json>
```

**Step 6 — Verify.** Read the printed delta. Then:

```bash
./brain.py stats
./brain.py audit
```

If `audit` warns about high `related_to` ratio or many orphans, revisit the
extraction. If it warns about node growth without matching relations, you
probably extracted entities without connecting them — go back to step 3.

**Step 7 — Gap verification.** This is the most important quality check.
It is cognitively different from extraction: you are no longer selecting,
you are comparing.

1. Retrieve every node extracted from this document:

```bash
./brain.py query "MATCH (n:Node) WHERE any(s IN n.sources WHERE s = '<doc_id>') RETURN n.label, n.type, n.description ORDER BY n.importance DESC"
```

2. Re-read the source document with the node list visible.

3. Go section by section. For each `##` section ask: *is the core content
   of this section represented in the graph — either as a dedicated node or
   clearly captured in an existing node's description?* If not, it is a gap.

4. Produce a gap list. For each gap decide:
   - **Extract now**: add the missing node(s) to the payload and re-ingest
     (`doc_id` re-ingestion is safe and idempotent).
   - **Skip with reason**: the section is implementation detail, already
     covered elsewhere, or a pure open question — write a one-line note.

5. Repeat until no unresolved gaps remain. Only then archive the source
   document.

**What gap verification catches that step 6 does not:** step 6 detects
structural problems (orphan nodes, bad `related_to` ratio). Gap verification
detects *semantic* omissions — a mechanism spread across three sections with
no dedicated node, a decision whose rationale was lost, a section the
extractor skimmed because it appeared late in the document. These are the
most common and most damaging extraction failures.

## Re-ingestion semantics

Re-running `ingest` on a payload whose `doc_id` already exists is **safe and
idempotent**. The ingester first purges every contribution of that `doc_id`
(removes it from `Rel.sources` and the matching evidence; deletes the edge if
`sources` becomes empty; nodes themselves are kept), then inserts the new
payload. A document update therefore replaces its previous contributions
cleanly, without disturbing relations introduced by other documents.

## Query workflow

Map the user's question to a CLI command:

| Question shape | Command |
|----------------|---------|
| "Why did X happen?" / "What causes X?" | `brain.py causes X` |
| "What does Y lead to?" / "If Y, then what?" | `brain.py effects Y` |
| "How does A relate to B?" / "Path from A to B?" | `brain.py paths A B` |
| "Show me node X" | `brain.py show X` |
| "Find nodes about <topic>" | `brain.py find <topic>` |
| Advanced traversal | `brain.py query "<cypher>"` |

When building the natural-language answer, **cite the `evidence`** that
appears in the CLI output. If a chain is built from low-confidence edges
(below ~0.6), flag that explicitly to the user — don't smuggle weak inferences
into a confident-sounding reply.

## Anti-patterns

- Inventing node types (e.g., `idea`, `risk`, `feeling`) — pick the closest
  whitelisted type or omit.
- Inventing relation types (e.g., `triggers`, `leads_to`, `influences`) —
  `causes` / `enables` / `precedes` almost always covers it.
- Over-using `related_to`. If you can't say *how* two things relate, skip the
  edge. The 5% ceiling is enforced by `audit`.
- Extracting nodes without any outgoing relation. Orphan nodes are dead
  weight; the audit flags them.
- Storing whole paragraphs in `description`. One disambiguating sentence is
  enough; the structure carries the rest.
- Mixing certain / inferred / speculative claims without marking confidence.

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
    {"id": "ttl_5_seconds", "label": "TTL 5 seconds", "type": "claim",
     "description": "Redis TTL configured at 5 seconds for hot keys."},
    {"id": "request_interval_30s", "label": "Request interval 30s", "type": "property",
     "description": "Median time between two requests on the same hot key."},
    {"id": "cache_miss_storm", "label": "Cache miss storm", "type": "event",
     "description": "Near-total cache miss rate on hot keys."},
    {"id": "db_fallback", "label": "Database fallback", "type": "event",
     "description": "Read traffic flowing to PostgreSQL when Redis misses."},
    {"id": "p99_latency_800ms", "label": "p99 latency 800ms", "type": "property",
     "description": "Observed 99th-percentile request latency."}
  ],
  "rels": [
    {"src": "ttl_5_seconds", "dst": "cache_miss_storm", "type": "causes", "confidence": 0.95,
     "evidence": "TTL shorter than mean inter-request time guarantees expiry between accesses."},
    {"src": "request_interval_30s", "dst": "cache_miss_storm", "type": "enables", "confidence": 0.9,
     "evidence": "Slow request rate amplifies the effect of a short TTL."},
    {"src": "cache_miss_storm", "dst": "db_fallback", "type": "causes", "confidence": 1.0,
     "evidence": "Cache miss => the read goes to the database."},
    {"src": "db_fallback", "dst": "p99_latency_800ms", "type": "causes", "confidence": 0.9,
     "evidence": "Direct database reads are ~10x slower than the cache layer here."}
  ]
}
```

Sample queries afterwards:

```bash
./brain.py causes p99_latency_800ms      # walks back through db_fallback → cache_miss_storm → ttl_5_seconds
./brain.py effects ttl_5_seconds         # walks forward to the latency outcome
./brain.py paths ttl_5_seconds p99_latency_800ms
```

## Reference examples

The repository ships `examples/sample.json` describing the causal anatomy of
how open-source projects die (18 nodes, 26 relations). Read it before
extracting your first document — it shows the granularity and tone expected.
