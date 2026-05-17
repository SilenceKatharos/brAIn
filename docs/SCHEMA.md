# brAIn — Graph Schema

The graph lives in a single Kuzu database (`graph/kuzu_db/` by default). It
uses exactly two tables: one for nodes, one for relations. The semantic type
is stored as a property — see [SKILL.md](SKILL.md) for the whitelist.

## Tables

### `Node`

| Column        | Type         | Notes |
|---------------|--------------|-------|
| `id`          | `STRING`     | Primary key. Always `slugify(label)`. |
| `label`       | `STRING`     | Human-readable label. |
| `type`        | `STRING`     | One of the node whitelist values. |
| `description` | `STRING`     | One-sentence disambiguation. |
| `importance`  | `DOUBLE`     | 0.0 to 1.0, default 0.5. Max-of observed values on update. |
| `created_at`  | `STRING`     | ISO-8601 UTC. |
| `updated_at`  | `STRING`     | ISO-8601 UTC. |
| `sources`     | `STRING[]`   | List of `doc_id`s that introduced or referenced the node. |

### `Rel` (FROM `Node` TO `Node`)

| Column        | Type         | Notes |
|---------------|--------------|-------|
| `type`        | `STRING`     | One of the relation whitelist values. |
| `confidence`  | `DOUBLE`     | 0.0 to 1.0, default 0.8. Max-of observed values on update. |
| `evidences`   | `STRING[]`   | Parallel array to `sources`: `evidences[i]` comes from `sources[i]`. |
| `sources`     | `STRING[]`   | List of `doc_id`s that asserted this edge. |
| `created_at`  | `STRING`     | ISO-8601 UTC. |
| `updated_at`  | `STRING`     | ISO-8601 UTC. |

Edges are **deduplicated on the triple `(src, dst, type)`**. Multiple
documents asserting the same edge accumulate their evidence and source in the
parallel arrays. Kuzu itself allows multiple edges between the same pair —
uniqueness is enforced at the application layer (`lib/ingest.py`).

## Slugify

The canonical id of a node is derived from its label via the rules in
`lib/slugify.py`:

1. NFKD-decompose and strip non-ASCII characters (e.g., `é` → `e`).
2. Lowercase.
3. Replace any run of non-alphanumerics by `_`.
4. Collapse repeated `_`.
5. Trim leading/trailing `_`.
6. Truncate to 80 characters, preferring to cut on the last `_` boundary
   in the second half of the result.

If a payload provides an `id` that differs from `slugify(label)`, the
ingester rewrites it to the canonical form and logs a warning entry in the
ingest report.

## Re-ingestion semantics

When a payload with a `doc_id` already present in the graph is re-ingested,
the pipeline runs three phases:

1. **Purge.** For every `Rel` whose `sources` contains the incoming
   `doc_id`, remove the matching position from `sources` and `evidences`.
   If the resulting `sources` is empty, the edge is deleted. Nodes are never
   deleted; their `sources` is trimmed but the node remains because other
   documents (or future ingestions) may still reference it.
2. **Upsert nodes.** Existing ids are merged: label refreshed, description
   filled if previously empty, importance set to the max of old and new,
   sources deduplicated and augmented with the new `doc_id`. New ids are
   created; substring overlap with existing labels logs to
   `potential_duplicates.jsonl`.
3. **Upsert relations.** Matching `(src, dst, type)` triples cumulate their
   evidence; confidence is set to the max. New triples are created.

The net effect is that re-ingesting the same payload is a strict no-op, and
updating a document atomically replaces its previous contributions without
disturbing relations introduced by other documents.

## Logs

Two append-only JSONL files are written to the project root by default:

- `extension_requests.jsonl` — out-of-whitelist node and relation types
  encountered during ingest. Use to decide if the whitelist should evolve.
- `potential_duplicates.jsonl` — substring overlaps between new nodes and
  existing ones. Use to drive manual `brain.py merge` decisions.

Both are git-ignored.

## Reserved aliases

Kuzu reserves several SQL-like keywords. When writing raw Cypher in
`brain.py query`, avoid using `desc`, `asc`, `limit`, `skip`, etc. as column
aliases — prefer `descr`, `direction`, etc.
