# Known limitations

Honest list of gaps and workarounds in the current implementation.

## Multi-word project names get truncated

Project names are derived from the directory basename by taking the
**first slugified token** (everything up to the first non-alphanumeric
character). A directory named `my-app` resolves to project `my`, not
`my-app`. Mirrors `derive_project_tag` in `lib/validate.py`.

**Workaround:** rename the directory to a single token, or ingest
manually with a custom `doc_id` for full control over the project tag.

**Root cause:** `derive_project_tag` uses regex
`^project_([a-z0-9]+)(?:_.+)?$`. Group 1 is `[a-z0-9]+` (no
underscores). Multi-token names would require either a convention
change (e.g. `__` as the name/aspect separator) or a different parser.

## Single-cwd assumption per session

Each Claude Code session is bound to ONE project — the cwd at
`SessionStart`. If you `cd` to a different project mid-session, the
sync agent still targets the original project's tree. There's no hook
that fires on `cd`, so re-detection isn't automatic.

**Workaround:** open a fresh Claude Code session when switching
projects.

## Sub-agents may still over-ingest

The `Agent` tool spawns sub-agents inside the main Claude session.
These sub-agents:
- DO inherit `~/.claude/CLAUDE.md` and the MCP brain tools
- DO NOT see the `UserPromptSubmit` reminder (it fires only on the main
  user's messages)

If `~/.claude/CLAUDE.md` says "extract into the graph", the sub-agent
may interpret this as license to run `brain_ingest` itself — competing
with the sync agent. **Mitigated** by the updated CLAUDE.md and the
explicit "RESERVED USAGE" warning on the `brain_ingest` MCP tool, but
not 100% eliminated.

**Workaround:** in the sub-agent's prompt, explicitly state "Do not
call brain_ingest yourself — the background sync agent handles graph
updates."

## No DELETE flow for stale projects

There's no `brain forget <project>` command. If you delete a project
directory, the graph nodes remain. The cache file
`/tmp/brain_known_projects.txt` still lists the project, so the
UserPromptSubmit hook still fires if you `cd` to a similarly-named
path.

**Workaround:** manual Cypher to purge:

```bash
brain query "MATCH (n:Node) WHERE 'project:<name>' IN n.sources DETACH DELETE n"
brain query "MATCH (n:Node) UNWIND n.sources AS s WITH s WHERE s STARTS WITH 'project:' RETURN DISTINCT s" \
  | jq -r '.[]."s"' | sed 's/^project://' > /tmp/brain_known_projects.txt
```

## Sync agent depends on `git`

The auto-register and sync agent both check for `.git/` and use
`git diff` against a HEAD baseline as the first-line diff source. A
project without git falls back to the PostToolUse queue, which only
captures Edit/Write tool invocations — Bash operations (`cp`, `mv`,
`sed`) escape the radar.

**Workaround:** `git init` the project. Even a single empty commit is
enough to enable the full diff pipeline.

## Sync agent costs API tokens

Every Stop with file changes triggers a `claude --print` call.
Incremental snapshots keep the per-call payload small (typically ~2 KB
of diff) but the cost is non-zero. On a busy day with 50 turns of
substantive edits, expect ~$0.20–$0.50 in API usage from the sync
agent alone (model-dependent).

**Workaround:** none — this is the cost of automated maintenance. The
alternative is the previous foreground-nag approach which we abandoned
because it was reliably ignored.

## UI layout simulation blocks the main thread

The d3-force simulation runs synchronously for 400 ticks when the
graph changes (expansion, search, ingest completion). On a 300-node
graph this takes ~500 ms during which the UI is unresponsive. Visible
as a brief freeze.

**Workaround:** the simulation is fast enough on modern hardware that
it's a perceptual nit rather than a usability blocker. A Web Worker
version exists in sketch form but isn't merged.

## The ingest screenshot is older than the graph one

`docs/screenshot_graph.png` was captured on 2026-05-20 with the current
graph state (318 nodes, all 4 projects visible). `docs/screenshot_ingest.png`
dates from 2026-05-17 — the ingest panel itself hasn't changed, so the
UX it shows is still accurate, but the bordering graph is older.

## Slug pitfall mitigated but not eliminated

`brain check` lints labels containing `()/.+` and emits warnings.
Labels with adjacent capitals (`CheckReport` → `checkreport`) or
trailing numbers (`512KB`) still slug differently than a human might
expect — and the rels referencing the pre-slug id are silently skipped
(or, with `brain check`, errored). The lint helps but doesn't catch
every case.

**Workaround:** always run `brain check` before `brain ingest`. The
strict MCP path enforces this automatically; the CLI does too unless
`--force` is passed.

## Tests cover `lib/` and `brain.py`, not UI or hooks

The pytest suite covers the ingestion engine (`lib/`) and the CLI. The
UI backend, frontend, and the 4 hook scripts (`brain_*.sh`,
`brain_stop_check.py`) have no automated coverage. End-to-end behavior
is validated manually via the Lumora and brain auto-extraction
experiments documented in the graph itself
(`brain show sync_agent_first_e2e_test_event`).
