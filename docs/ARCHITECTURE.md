# Architecture

brAIn has two cooperating processes: a working session (you + Claude) that
**queries** the graph, and a background **sync agent** that **maintains** it.
The split exists because mixing the two roles produces incoherent graph
state — observed empirically across several sessions.

## The four hooks

Registered globally in `~/.claude/settings.json` (printed by `install.sh`):

| Hook | When it fires | What it does |
|---|---|---|
| `SessionStart` | Claude Code opens a session | Captures `git HEAD` of the cwd, surfaces the previous sync status, auto-registers a stub if the project is new |
| `PostToolUse` | After every `Edit` / `Write` | Appends the file path to `/tmp/brain_session_modified.txt` (sync agent's fallback queue) |
| `UserPromptSubmit` | Every user message | Injects a one-line reminder of the QUERY-first protocol if cwd is a tracked project |
| `Stop` | Claude finishes a turn | Spawns `brain_sync_agent.sh` detached with the session cwd as `TARGET_DIR` |

The four scripts at the repo root are intentionally short (each <50
lines, except the sync agent itself). The PostToolUse hook is silent —
it doesn't nag — because the sync agent will pick the work up at Stop.

## The sync agent

`brain_sync_agent.sh` runs **detached** (via `nohup` + `start_new_session`)
so the foreground session never waits on graph maintenance.

```
brain_sync_agent.sh <TARGET_DIR>
  ↓
  derive PROJECT_NAME = slugify(basename(TARGET_DIR), first token)
  ↓
  acquire flock /tmp/brain_sync_<PROJECT_NAME>.lock (single-instance)
  ↓
  build diff bundle from THREE sources, in order:
    1. /tmp/brain_sync_baseline/<PROJECT_NAME>/   (incremental snapshot)
    2. git diff vs SESSION_HEAD                   (first-sync fallback)
    3. /tmp/brain_session_modified.txt            (queue, last resort)
  ↓
  if no diffs: write status "synced — no changes", exit fast (<100 ms)
  ↓
  invoke `claude --print` with the diff + extraction prompt
  ↓
  parse JSON payload between <<<BRAIN_PAYLOAD>>> markers
  ↓
  run `brain ingest --no-causal-check <payload>`
  ↓
  if ingest exit <= 1:
      refresh_snapshot() (atomic rename)
      write status SUMMARY: N nodes / M rels added
  else:
      write status NEEDS_REVIEW + dump output to review file
```

### Per-project state

Each tracked project gets its own state files so two parallel sessions
in different projects never collide:

```
/tmp/brain_sync_baseline/<project>/        snapshot mirror (atomic)
/tmp/brain_sync_git_head_<project>.txt     HEAD captured at SessionStart
/tmp/brain_sync_status_<project>.txt       last sync result line
/tmp/brain_sync_review_<project>.txt       failed-ingest review queue
/tmp/brain_sync_<project>.lock             single-instance flock
/tmp/brain_known_projects.txt              cache (read by UserPromptSubmit)
```

The Kuzu graph itself is **shared**: every project's nodes carry a
`project:<name>` source tag (auto-injected from the `doc_id`), so a
single Cypher filter isolates them at query time.

### Incremental snapshot — the cost optimization

After every successful sync, `refresh_snapshot()` mirrors the project's
relevant files (size ≤ 512 KB, non-binary extensions) under
`/tmp/brain_sync_baseline/<project>/`. The next sync compares the
current working tree to this snapshot file-by-file.

Measured impact on real Claude Code sessions:

| Sync cycle | Diff bundle sent to LLM | Wall time |
|---|---|---|
| First sync (no snapshot yet) | 91 KB | 2m 05s |
| Subsequent sync (1 edit) | 3.6 KB (-96%) | 1m 16s |
| Subsequent sync (no edit) | 0 KB | **68 ms, no API call** |

## Auto-registration of new projects

When you open Claude Code in a directory you've never used before:

1. **SessionStart hook** detects `.git/` → derives project name from the
   cwd basename (first slugified token).
2. Checks `/tmp/brain_known_projects.txt` — if the name isn't there:
3. Writes a stub payload to a tmp file:
   ```json
   {
     "doc_id": "project_<name>",
     "nodes": [{
       "label": "<DirName>",
       "type": "artifact",
       "importance": 0.5,
       "description": "Project at <path>. Auto-registered ..."
     }],
     "rels": []
   }
   ```
4. Runs `brain ingest --no-causal-check` on the stub.
5. The ingest auto-refreshes `/tmp/brain_known_projects.txt` to include
   the new name.
6. Creates the empty `/tmp/brain_sync_baseline/<name>/` subdirectory.

Total cost: one `brain ingest` invocation on a 1-node payload
(~500 ms once per project, never again).

After this, the UserPromptSubmit hook fires on the very next message
and the sync agent has a stub to enrich at the next Stop.

## Recursion guard

The sync agent spawns `claude --print`, which is itself a Claude
Code-style session. If that inner Claude edited files, it would trigger
the PostToolUse hook → queue → next Stop → another sync agent →
infinite recursion.

Prevented by an environment variable:

```bash
BRAIN_HOOK_DISABLED=1 claude --print < prompt
```

`brain_hook.sh` and `brain_stop_check.py` both check this variable at
the top and exit immediately when set. The sync agent exports it before
calling the inner Claude. Standard shell scoping ensures the inner
Claude inherits it.

## Why background, not foreground

The first version of brAIn had the Stop hook **block** the user, asking
Claude to update the graph inline. Observed empirically: the working
Claude (including very capable models) **systematically procrastinated**
the graph update — there's always something more pressing in the next
turn.

The background sync agent isn't a constraint, it's an **offload**: the
maintenance task moves to a process whose only job is maintenance. The
working session is unburdened. The architecture choice and its rejected
alternative are themselves captured in the graph as a `contradicts`
edge:

```bash
brain show synchronous_stop_nag_approach
brain causes background_graph_sync_architecture
```

## Two trust models: CLI vs MCP

The CLI accepts a `--force` flag that bypasses the strict pre-ingest
check. The MCP `brain_ingest` tool has **no equivalent** — by design.

| Interface | Audience | Override |
|---|---|---|
| `brain.py ingest` | Human at a terminal | `--force` allowed |
| MCP `brain_ingest` | Claude (or any LLM in a tool loop) | No override; fix the payload |

A human can take responsibility for forcing an ingest. An LLM in a tool
loop probably can't — and would silently overwrite half the graph if
allowed to.
