# Ingestion

How payloads flow from JSON file to graph storage. Most users never write
a payload by hand — the background sync agent does it automatically from
diffs. This page documents the format for the cases where you do
(user-invoked `/ingest`, manual extractions, or debugging).

## Payload format

```json
{
  "doc_id": "redis_postmortem_2026_q1",
  "nodes": [
    {
      "label": "TTL too short",
      "type": "claim",
      "description": "Redis TTL set to 5s for hot keys whose median inter-request interval is 30s.",
      "importance": 0.85
    },
    {
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

### `doc_id`

Required, string. Convention: `project_<name>_<aspect>` (e.g.
`project_brain_lib`, `project_lumora_themes`) or `project_<name>` for
unified docs. The auto-tag mechanism extracts `<name>` and injects
`project:<name>` into every node and rel of this doc.

Re-ingesting an existing `doc_id` is **idempotent**: previous
contributions are purged before the new payload is written.

### `nodes[]`

| Field | Required | Notes |
|---|---|---|
| `label` | yes | Human-readable. ASCII recommended (see slug rules). |
| `type` | yes | Open vocabulary (any string accepted), unknown types logged to `extension_requests.jsonl`. See [Vocabulary](VOCABULARY.md). |
| `description` | recommended | 30–400 chars. The validator lints both extremes. |
| `id` | optional | Defaults to `slugify(label)`. Mismatches are auto-corrected. |
| `importance` | optional | Float `0.0–1.0`, default `0.5`. Used by the UI for visibility threshold. |
| `sources` | optional | Extra source tags; `project:<name>` is auto-injected. |

### `rels[]`

| Field | Required | Notes |
|---|---|---|
| `src` | yes | Node id (or label — slugified) of the source endpoint. |
| `dst` | yes | Node id of the destination endpoint. |
| `type` | yes | Strict whitelist — see [Vocabulary](VOCABULARY.md#relation-types). |
| `evidence` | recommended | 30+ chars explaining the mechanism. Lint warns below 30. |
| `confidence` | optional | Float `0.0–1.0`, default `0.8`. See [Vocabulary](VOCABULARY.md#confidence-calibration). |
| `factor` | optional | Quantitative magnitude (e.g. "10x slowdown"). Free-form string. |

## Slug rules (critical)

Node `id` is canonicalized via `slugify(label)`:

```
lowercase → replace every run of non-[a-z0-9] with a single underscore
```

Examples that bite:

| Label | Slug |
|---|---|
| `"TTL 5 seconds"` | `ttl_5_seconds` |
| `"CheckReport dataclass"` | `checkreport_dataclass` *(not `check_report_dataclass`)* |
| `"Reliability sigmoid G(f,d)"` | `reliability_sigmoid_g_f_d` |
| `"Sync file size cap 512KB"` | `sync_file_size_cap_512kb` |
| `"H/C ratio v0.5"` | `h_c_ratio_v0_5` |

**The trap:** if you write a label with adjacent capitals or trailing
numbers/units, the slug isn't what you'd guess. Any rel referencing the
pre-slug id is silently skipped (or, with the v2 fail-loud, errors at
`brain check`).

**Rules of thumb:**
1. Use plain ASCII labels with no `()`, `/`, `.`, `+`.
2. Either omit `id` (let the validator derive it) or set it to the exact
   `slugify(label)` output.
3. After ingest, run `brain find <fragment>` to confirm the actual id.

The validator's lint catches forbidden chars at `brain check` time and
warns before the rel-skip cascade.

## Idempotent re-ingestion

Re-running `brain ingest` with the same `doc_id` is **safe and explicit**.

1. **Purge phase**: for every node and rel whose `sources` contains this
   `doc_id`, remove the doc_id (and its parallel `evidences`/`factors`
   entries for rels). If a rel's `sources` becomes empty, delete the rel.
   **Nodes are never deleted** — only their participation in this doc.
2. **Upsert phase**: insert/merge as a fresh ingest of the same payload.

Net effect: the doc's contribution is replaced cleanly without
disturbing contributions from other docs that share the same nodes.

Practical use: update a payload file, re-run `brain ingest <file>`. No
deletes, no migrations.

## Validation gates

`brain ingest` runs `lib.check.check_payload` before any write. The
pre-ingest check reports:

**Errors** (block ingest unless `--force`):
- `rejected nodes`: missing label, missing type, unslugifiable label.
- `rejected rels`: unknown rel type, self-loop, missing endpoints in the payload.
- `missing endpoints`: rel references a node id absent from both the payload AND the existing graph.
- `causal check failed`: payload has ≥ 5 rels but 0 of `causes` / `prevents` / `contradicts`.

**Warnings** (proceed but exit 1):
- `lint`: description too long (>400) or too short (<30), evidence too short (<30), label contains forbidden chars `()/.+`.
- `rewritten ids`: proposed id ≠ `slugify(label)`.
- `potential duplicates`: substring match against existing graph nodes.

Post-ingest, a health summary prints automatically:

```
# Post-ingest health
  total: 318 nodes, 398 rels
  causal 30% | structural 60% | tradeoff 4% | orphans 3% | related_to 0%
```

## Parallel arrays invariant

Each `Rel` stores `sources`, `evidences`, `factors` as parallel arrays.
Index `i` is one contributing source: `sources[i]` is the doc_id,
`evidences[i]` is the sentence explaining the mechanism from that doc's
perspective, `factors[i]` is the optional magnitude.

**Invariant:** `len(sources) == len(evidences) == len(factors)`. The
audit flags violations as an integrity bug. The helper
`_assemble_rel_arrays` in `lib/ingest.py` enforces this by construction
on both the create and update branches.

When multiple docs contribute the same edge (same `src+dst+type`), the
arrays grow — each doc contributes one entry. Project tags (e.g.
`project:brain`) appear in `sources` but contribute empty `evidences`
and `factors` since they're metadata, not provenance with a sentence.

## What happens during a sync-agent ingest

The sync agent's ingest call is functionally identical to a manual
`brain ingest`, with two differences:

1. **`--no-causal-check` flag**: incremental syncs are often purely
   structural (a new function, a new flag); the causal-balance check is
   inappropriate at that granularity.
2. **`BRAIN_HOOK_DISABLED=1` env**: silences the PostToolUse/Stop hooks
   so the inner Claude's writes don't trigger another sync agent.

See [Architecture](ARCHITECTURE.md#the-sync-agent).
