# Vocabulary

The graph has two vocabularies: **strict** for relations (semantic
core), **open** for node types (flexibility). Both are documented for
reference, but the **validator enforces them at ingest time** — see
[Ingestion](INGESTION.md).

## Relation types — strict whitelist

Anything outside this list is **rejected** at ingest and logged to
`extension_requests.jsonl`.

### Causal core (prefer these)

| Type | Meaning | Example |
|---|---|---|
| `causes` | A produces B (factual or statistical) | TTL too short — `causes` → cache miss storm |
| `prevents` | A blocks B from happening | Rate limit — `prevents` → load shedding |
| `requires` | B cannot exist without A | OAuth flow — `requires` → token endpoint |
| `enables` | A makes B possible (without forcing) | SIMD support — `enables` → fast literal scan |
| `precedes` | A happens before B (temporal only, no causation) | Session start — `precedes` → first user prompt |
| `contradicts` | A and B are logically incompatible | Synchronous nag — `contradicts` → background sync |

The graph's value comes from these. The `causal_ratio` audit metric
measures `(causes + prevents + enables + contradicts) / total_rels` and
warns below 15%.

### Structural support

| Type | Meaning |
|---|---|
| `is_a` | A is a kind of B (taxonomy) |
| `part_of` | A is a component of B (composition) |
| `instance_of` | A is a concrete instance of B |
| `similar_to` | A resembles B without being an instance |
| `property_of` | A is a property of B |

These build the skeleton. The `structural_dominance` audit metric
measures `(part_of + requires) / total_rels` and warns above 70% (the
graph is too arborescent, not causal enough).

### Fallback (avoid)

| Type | Meaning |
|---|---|
| `related_to` | Unqualified link |

If you can't say *how* two things relate, skip the edge entirely. The
audit warns when `related_to` exceeds 5% of total edges.

## Node types — open vocabulary

Any non-empty string accepted. Unknown types are logged to
`extension_requests.jsonl` for human review but the node is still
created. Coherence comes from lookup-before-create (`brain_find`), not
from type enforcement.

**Reference vocabulary** (when none of these fits, pick the closest or
invent a meaningful new string):

| Type | When to use |
|---|---|
| `concept` | Abstract idea (eventual consistency, idempotency) |
| `entity` | Named, identifiable thing (PostgreSQL, AWS, Kuzu) |
| `event` | Something that happens in time (2024 outage, cache miss storm) |
| `claim` | A debatable assertion (cache invalidates too often) |
| `mechanism` | A process that turns a cause into an effect |
| `algorithm` | A named computational procedure (backpropagation, k-means) |
| `property` | A measurable / qualifiable attribute (p99 latency, throughput) |
| `artifact` | A produced object (code, document, system, schema) |
| `process` | A goal-directed sequence of steps |
| `person` | A human actor |
| `place` | A location |

## Confidence calibration

Each rel carries a `confidence` float between 0 and 1. Calibrate per
this rubric:

| Value | Meaning |
|---|---|
| `1.0` | Explicitly stated in the text with a direct causal verb |
| `0.7–0.9` | Reasonable inference from the text |
| `0.4–0.6` | Plausible hypothesis, not demonstrated |
| `< 0.4` | Don't ingest — better to omit than to pollute |

The `brain audit` command flags chains containing edges below 0.6 so
queries like `brain causes X` can warn when the chain is weak.

## Why this split

| | Node types (open) | Rel types (strict) |
|---|---|---|
| **Enforced** | No — logged only | Yes — rejected at ingest |
| **Used by traversal** | No — purely descriptive | Yes — `brain causes` walks the causal subset |
| **Rationale** | Flexibility — domains differ wildly | Causal semantics must not drift |

Node types help a reader filter and orient ("show me only `algorithm`
nodes"). Rel types are the **load-bearing structure** of the graph —
`brain_causes` and `brain_effects` only walk specific subsets:

- `CAUSAL_FORWARD = causes, enables, precedes`
- `CAUSAL_BACKWARD = causes, prevents, requires, enables, precedes`

If `causes` were a free string, `brain causes` would have no defined
behavior. That's why the whitelist is non-negotiable.
