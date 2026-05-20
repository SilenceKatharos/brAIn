#!/usr/bin/env bash
# brain_sync_agent.sh — background graph-sync worker
#
# Spawned by brain_stop_check.py when the user yields control. Reads the list
# of files modified during the session, computes their git diffs, asks a
# headless `claude --print` invocation to extract structural changes and
# update the graph via the brAIn MCP tools, then writes a status line.
#
# This script never blocks the foreground session. All output goes to
# /tmp/brain_sync_status.txt and /tmp/brain_sync_log.txt.

set -uo pipefail

BRAIN_DIR="$(cd "$(dirname "$0")" && pwd)"

# TARGET_DIR is the project to sync, passed as arg 1 by the Stop hook (the
# user's session cwd). When called without args (e.g. manual invocation),
# default to BRAIN_DIR — preserves backwards compat for one-off testing
# inside the brAIn repo itself.
TARGET_DIR="${1:-$BRAIN_DIR}"
TARGET_DIR="$(cd "$TARGET_DIR" 2>/dev/null && pwd)"
if [ -z "$TARGET_DIR" ] || [ ! -d "$TARGET_DIR" ]; then
    echo "[$(date -Iseconds)] invalid TARGET_DIR='${1:-}', aborting" >> /tmp/brain_sync_log.txt
    exit 0
fi

# Project name = first slugified token of TARGET_DIR's basename. Matches
# derive_project_tag so per-project files line up with the graph tag.
PROJECT_NAME="$(basename "$TARGET_DIR" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9].*//')"
if [ -z "$PROJECT_NAME" ]; then
    echo "[$(date -Iseconds)] could not derive project name from $TARGET_DIR" >> /tmp/brain_sync_log.txt
    exit 0
fi

# Per-project state files. The session-wide queue (MODIFIED_FILE) stays
# global because it's populated by the PostToolUse hook which has no notion
# of project; the agent filters it to entries under TARGET_DIR.
MODIFIED_FILE="/tmp/brain_session_modified.txt"
SESSION_HEAD_FILE="/tmp/brain_sync_git_head_${PROJECT_NAME}.txt"
SNAPSHOT_DIR="/tmp/brain_sync_baseline/${PROJECT_NAME}"
STATUS_FILE="/tmp/brain_sync_status_${PROJECT_NAME}.txt"
LOG_FILE="/tmp/brain_sync_log.txt"  # shared, helps cross-project debugging
LOCK_FILE="/tmp/brain_sync_${PROJECT_NAME}.lock"
REVIEW_FILE="/tmp/brain_sync_review_${PROJECT_NAME}.txt"

# Filter that excludes noise paths from snapshots and diff bundles. Keep
# centralized so SNAPSHOT and DIFF use the exact same set of files.
SYNC_EXCLUDE_REGEX='^(\.venv/|node_modules/|graph/kuzu_db/|.*/__pycache__/|\.pytest_cache/|projects/|ui/frontend/dist/|examples/|experiments/ai_wiki/(corpus|extractions|results)/)'
# Binary / heavyweight extensions excluded — never carry structural meaning.
SYNC_EXCLUDE_EXT='\.(png|jpg|jpeg|gif|ico|svg|pdf|zip|gz|tar|whl|so|pyc|woff2?|ttf|mp[34]|webm|sqlite|db|kuzu)$'
SYNC_MAX_BYTES=524288  # 512 KB; above this a file is treated as out-of-scope

# Yield the list of files (relative to BRAIN_DIR) we care about syncing.
# Excludes:
#  - noise directories listed in SYNC_EXCLUDE_REGEX
#  - binary / heavyweight extensions
#  - files larger than SYNC_MAX_BYTES (typically generated/data artifacts)
sync_relevant_files() {
    {
        git ls-files
        git ls-files --others --exclude-standard
    } 2>/dev/null | grep -vE "$SYNC_EXCLUDE_REGEX" | grep -vE "$SYNC_EXCLUDE_EXT" | while read -r f; do
        [ -f "$f" ] || continue
        local size
        size="$(stat -c '%s' "$f" 2>/dev/null || echo 0)"
        [ "$size" -le "$SYNC_MAX_BYTES" ] && echo "$f"
    done
}

# Single-instance lock so concurrent Stops don't double-fire
exec 9>"$LOCK_FILE"
flock -n 9 || { echo "[$(date -Iseconds)] sync already running, skipping" >> "$LOG_FILE"; exit 0; }

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[$(ts)] $*" >> "$LOG_FILE"; }

# Refresh the incremental baseline. Called after a sync that successfully
# advanced the graph (ingest passed or no structural changes found). Captures
# every file we care about, so the next sync's diff is incremental.
# Atomic via temp dir + rename — partial snapshots can't corrupt the baseline.
refresh_snapshot() {
    local tmp="$SNAPSHOT_DIR.tmp.$$"
    rm -rf "$tmp"
    mkdir -p "$tmp"
    local count=0
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        [ -f "$f" ] || continue
        # Skip files larger than 512KB (binary, generated, lockfiles)
        local size
        size="$(stat -c '%s' "$f" 2>/dev/null || echo 0)"
        [ "$size" -gt 524288 ] && continue
        mkdir -p "$tmp/$(dirname "$f")"
        cp "$f" "$tmp/$f" 2>/dev/null && count=$((count + 1))
    done < <(sync_relevant_files)
    rm -rf "$SNAPSHOT_DIR"
    mv "$tmp" "$SNAPSHOT_DIR"
    log "snapshot refreshed: $count files captured in $SNAPSHOT_DIR"
}

log "=== sync agent start: project=$PROJECT_NAME target=$TARGET_DIR ==="

# ── 1. Build the diff bundle ──────────────────────────────────────────────
# Source preference, in order:
#  (a) Incremental snapshot at /tmp/brain_sync_baseline/<project>/ (last
#      successful sync captured here). Compare current files vs snapshot —
#      minimal diff regardless of which tool produced the changes. Refreshed
#      after every successful ingest so each sync only sees what's new.
#  (b) git diff vs session HEAD — first-sync-of-session fallback when no
#      snapshot exists yet.
#  (c) PostToolUse modification queue, filtered to files under TARGET_DIR —
#      last-resort fallback for non-git repos.
DIFF_BUNDLE="$(mktemp /tmp/brain_sync_diff.XXXXXX)"
cd "$TARGET_DIR" || { log "cannot cd to $TARGET_DIR"; exit 1; }

HAVE_DIFFS=0
USED_SOURCE="none"

# ── (a) Incremental snapshot comparison (preferred) ───────────────────────
if [ -d "$SNAPSHOT_DIR" ]; then
    log "comparing working tree against snapshot at $SNAPSHOT_DIR"
    SEEN_FILES="$(mktemp /tmp/brain_sync_seen.XXXXXX)"
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        [ -f "$f" ] || continue
        echo "$f" >> "$SEEN_FILES"
        snap="$SNAPSHOT_DIR/$f"
        if [ -f "$snap" ]; then
            if ! cmp -s "$f" "$snap"; then
                echo "===== CHANGED: $f =====" >> "$DIFF_BUNDLE"
                diff -u "$snap" "$f" 2>/dev/null | head -800 >> "$DIFF_BUNDLE"
                echo "" >> "$DIFF_BUNDLE"
                HAVE_DIFFS=1
            fi
        else
            echo "===== NEW: $f =====" >> "$DIFF_BUNDLE"
            head -c 32000 "$f" >> "$DIFF_BUNDLE"
            echo "" >> "$DIFF_BUNDLE"
            HAVE_DIFFS=1
        fi
    done < <(sync_relevant_files)
    # Detect deleted files: in snapshot but not in current tree.
    while IFS= read -r snap; do
        rel="${snap#$SNAPSHOT_DIR/}"
        [ -z "$rel" ] && continue
        if ! grep -qxF "$rel" "$SEEN_FILES" 2>/dev/null; then
            echo "===== DELETED: $rel =====" >> "$DIFF_BUNDLE"
            echo "(file was present at last sync, now absent — drop or update related graph nodes)" >> "$DIFF_BUNDLE"
            echo "" >> "$DIFF_BUNDLE"
            HAVE_DIFFS=1
        fi
    done < <(find "$SNAPSHOT_DIR" -type f 2>/dev/null)
    rm -f "$SEEN_FILES"
    USED_SOURCE="incremental snapshot"
fi

# ── (b) First-sync fallback: git diff vs session HEAD ─────────────────────
if [ "$HAVE_DIFFS" -eq 0 ] && [ "$USED_SOURCE" = "none" ] && [ -d "$TARGET_DIR/.git" ] && [ -s "$SESSION_HEAD_FILE" ]; then
    SESSION_HEAD="$(cat "$SESSION_HEAD_FILE")"
    if git rev-parse "$SESSION_HEAD" >/dev/null 2>&1; then
        log "no snapshot yet — diffing working tree against session HEAD $SESSION_HEAD"
        TRACKED_DIFF="$(git diff "$SESSION_HEAD" 2>/dev/null)"
        if [ -n "$TRACKED_DIFF" ]; then
            echo "$TRACKED_DIFF" >> "$DIFF_BUNDLE"
            HAVE_DIFFS=1
        fi
        while IFS= read -r f; do
            [ -z "$f" ] && continue
            [ -f "$f" ] || continue
            echo "" >> "$DIFF_BUNDLE"
            echo "===== NEW UNTRACKED FILE: $f =====" >> "$DIFF_BUNDLE"
            head -c 32000 "$f" >> "$DIFF_BUNDLE"
            echo "" >> "$DIFF_BUNDLE"
            HAVE_DIFFS=1
        done < <(sync_relevant_files | grep -vFxf <(git ls-files 2>/dev/null) 2>/dev/null || true)
        USED_SOURCE="git diff vs session HEAD"
    fi
fi

# ── (c) Last resort: PostToolUse queue, filtered to TARGET_DIR ────────────
if [ "$HAVE_DIFFS" -eq 0 ] && [ "$USED_SOURCE" = "none" ] && [ -s "$MODIFIED_FILE" ]; then
    log "no git path available — falling back to PostToolUse queue (filtered to $TARGET_DIR)"
    mapfile -t FILES < <(sort -u "$MODIFIED_FILE" | grep -v '^$' | grep -F "$TARGET_DIR/")
    for f in "${FILES[@]}"; do
        [ -e "$f" ] || continue
        if git -C "$(dirname "$f")" rev-parse --git-dir >/dev/null 2>&1; then
            DIFF="$(git -C "$(dirname "$f")" diff HEAD -- "$f" 2>/dev/null)"
            if [ -n "$DIFF" ]; then
                echo "===== FILE: $f =====" >> "$DIFF_BUNDLE"
                echo "$DIFF" >> "$DIFF_BUNDLE"
                echo "" >> "$DIFF_BUNDLE"
                HAVE_DIFFS=1
            fi
        fi
    done
    USED_SOURCE="queue + git diff HEAD"
fi

if [ "$HAVE_DIFFS" -eq 0 ]; then
    log "no diffs to sync (working tree matches the baseline)"
    echo "[$(ts)] synced — no changes since last sync" > "$STATUS_FILE"
    : > "$MODIFIED_FILE"
    rm -f "$DIFF_BUNDLE"
    exit 0
fi

log "diff bundle built via $USED_SOURCE: $(wc -l < "$DIFF_BUNDLE") lines, $(wc -c < "$DIFF_BUNDLE") bytes"

# ── 3. Spawn headless Claude to sync the graph ────────────────────────────
DOC_ID="project_${PROJECT_NAME}_sync_$(date -u +%Y_%m_%d)"
PROMPT_FILE="$(mktemp /tmp/brain_sync_prompt.XXXXXX)"
cat > "$PROMPT_FILE" <<EOF
You are the brAIn graph sync agent. You run in the background after the user
yields control. Your only job: extract the structural changes from the diff
below and emit a brAIn ingestion payload on stdout. The wrapper script will
handle file writes, validation, and ingestion. You DO NOT write files.

You DO have access to MCP brain tools — use \`brain_find\` (read-only) to
check whether equivalent nodes already exist before minting new ids. Reuse
existing ids verbatim when the semantic matches.

WHAT COUNTS AS STRUCTURAL:
- new functions/classes/constants with semantic meaning
- new modules, MCP tools, CLI commands, hooks
- new validators, mechanisms, thresholds
- explicit design decisions, tradeoffs, rejected alternatives
- bug fixes that revealed a previously-implicit invariant

IGNORE: whitespace, pure-rename, comment-only edits, formatting, line-shuffling.

OUTPUT FORMAT (strict):
1. Optional thinking in plain text (will be ignored by the wrapper).
2. EXACTLY ONE JSON payload between these two markers, with no markdown
   code fences, no commentary inside the markers:

<<<BRAIN_PAYLOAD>>>
{
  "doc_id": "$DOC_ID",
  "nodes": [...],
  "rels": [...]
}
<<<END_BRAIN_PAYLOAD>>>

3. After the closing marker, output exactly one final line that starts with
   either "SUMMARY: " (followed by "N nodes / M rels added"), or
   "NO_CHANGES" (if no structural changes were found — emit an empty payload
   with empty arrays in that case), or "NEEDS_REVIEW: " followed by a
   one-sentence reason.

The doc_id is fixed: $DOC_ID. The wrapper auto-injects "project:${PROJECT_NAME}"
— do not add it manually. The project being synced is "${PROJECT_NAME}" at
${TARGET_DIR}. Keep descriptions one-sentence (30-400 chars), labels plain
ASCII (no ()/.+), and evidence specific ("X causes Y because Z").

SLUG RULE (CRITICAL — silent rel skip otherwise): the node id MUST equal
slugify(label), which lowercases the label and replaces every run of
non-[a-z0-9] with a single underscore. Adjacent capitals collapse, numbers
attach to surrounding letters. Examples that BITE:
  label "CheckReport dataclass"  → slug "checkreport_dataclass"   (not check_report)
  label "Sync file size cap 512KB" → slug "sync_file_size_cap_512kb"
  label "k8s setup"              → slug "k8s_setup"
For each node, either set id to the exact slug, OR rename the label so its
slug matches the id you want. THEN make sure every rel's src/dst references
exactly that final id. If unsure, omit id and let the validator derive it,
but DO reference the resulting slug consistently in rels.

=== DIFF BUNDLE ===
EOF
cat "$DIFF_BUNDLE" >> "$PROMPT_FILE"

log "invoking claude --print (prompt size: $(wc -c < "$PROMPT_FILE") bytes)"

# Run headless Claude with hooks disabled in env (prevents recursion: the
# inner Claude would otherwise trigger our own PostToolUse / Stop hooks).
CLAUDE_OUTPUT="$(mktemp /tmp/brain_sync_out.XXXXXX)"
if BRAIN_HOOK_DISABLED=1 timeout 300 claude --print < "$PROMPT_FILE" > "$CLAUDE_OUTPUT" 2>>"$LOG_FILE"; then
    CLAUDE_EXIT=0
else
    CLAUDE_EXIT=$?
fi

if [ "$CLAUDE_EXIT" -ne 0 ]; then
    log "claude --print failed (exit $CLAUDE_EXIT)"
    echo "[$(ts)] sync FAILED: claude --print exit $CLAUDE_EXIT" > "$STATUS_FILE"
    cp "$CLAUDE_OUTPUT" "$REVIEW_FILE"
    : > "$MODIFIED_FILE"
    rm -f "$DIFF_BUNDLE" "$PROMPT_FILE" "$CLAUDE_OUTPUT"
    exit 0
fi

# ── 4. Extract the JSON payload between the markers ───────────────────────
PAYLOAD_FILE="$(mktemp /tmp/brain_sync_payload.XXXXXX.json)"
awk '/<<<BRAIN_PAYLOAD>>>/{flag=1;next} /<<<END_BRAIN_PAYLOAD>>>/{flag=0} flag' \
    "$CLAUDE_OUTPUT" > "$PAYLOAD_FILE"

# Final status line from the agent
STATUS_LINE="$(grep -E '^(SUMMARY:|NO_CHANGES|NEEDS_REVIEW:)' "$CLAUDE_OUTPUT" | tail -n 1)"
[ -z "$STATUS_LINE" ] && STATUS_LINE="NEEDS_REVIEW: agent produced no final status line"

SNAPSHOT_REFRESH=0  # set to 1 when the sync succeeded — refresh AFTER ingest

case "$STATUS_LINE" in
    NO_CHANGES)
        log "agent: no structural changes detected"
        echo "[$(ts)] synced — no structural changes detected" > "$STATUS_FILE"
        rm -f "$REVIEW_FILE"
        SNAPSHOT_REFRESH=1
        ;;
    SUMMARY:*)
        # Validate the extracted payload is non-empty JSON
        if [ ! -s "$PAYLOAD_FILE" ] || ! jq -e . "$PAYLOAD_FILE" >/dev/null 2>&1; then
            log "agent: SUMMARY but payload is empty or malformed JSON"
            echo "[$(ts)] sync NEEDS REVIEW: $STATUS_LINE but payload missing or malformed" > "$STATUS_FILE"
            cp "$CLAUDE_OUTPUT" "$REVIEW_FILE"
        else
            log "running brain ingest on extracted payload"
            CHECK_OUT="$(mktemp /tmp/brain_sync_check.XXXXXX)"
            # Sync agent ingests are incremental and often purely structural
            # (new function, new constant, new flag). The causal-balance precondition
            # is calibrated for full-document extractions — skipping it here is OK
            # because the agent ingests run constantly and any missing causal
            # nuance gets captured on the next non-trivial sync.
            BRAIN_HOOK_DISABLED=1 "$BRAIN_DIR/.venv/bin/python" "$BRAIN_DIR/brain.py" ingest --no-causal-check "$PAYLOAD_FILE" > "$CHECK_OUT" 2>&1
            INGEST_EXIT=$?
            # brain.py ingest exit codes:
            #   0 = clean ingest, no issues
            #   1 = lint warnings (description length, evidence length, rewritten ids) — data WAS written
            #   2 = real failure (rejected nodes/rels, skipped rels — data loss in this ingest)
            # We treat 0 and 1 as success, 2 as real failure that needs review.
            HEALTH="$(grep -E 'causal [0-9]+%' "$CHECK_OUT" | head -1 | sed 's/^[[:space:]]*//')"
            if [ "$INGEST_EXIT" -le 1 ]; then
                if [ "$INGEST_EXIT" -eq 0 ]; then
                    log "ingest clean: $STATUS_LINE | $HEALTH"
                    echo "[$(ts)] $STATUS_LINE — $HEALTH" > "$STATUS_FILE"
                else
                    LINT_COUNT="$(grep -cE 'lint warning|rewritten id' "$CHECK_OUT" || echo 0)"
                    log "ingest success with lint warnings ($LINT_COUNT): $STATUS_LINE | $HEALTH"
                    echo "[$(ts)] $STATUS_LINE — $HEALTH (with lint)" > "$STATUS_FILE"
                fi
                rm -f "$REVIEW_FILE"
                SNAPSHOT_REFRESH=1
            else
                log "ingest refused (exit $INGEST_EXIT, real data loss) — see review"
                echo "[$(ts)] sync NEEDS REVIEW: $STATUS_LINE but ingest refused (see /tmp/brain_sync_review.txt)" > "$STATUS_FILE"
                {
                    echo "=== Claude output ==="
                    cat "$CLAUDE_OUTPUT"
                    echo ""
                    echo "=== Ingest output (exit $INGEST_EXIT) ==="
                    cat "$CHECK_OUT"
                    echo ""
                    echo "=== Payload at $PAYLOAD_FILE ==="
                    cat "$PAYLOAD_FILE"
                } > "$REVIEW_FILE"
            fi
            rm -f "$CHECK_OUT"
        fi
        ;;
    NEEDS_REVIEW:*)
        log "agent flagged review: $STATUS_LINE"
        echo "[$(ts)] sync $STATUS_LINE" > "$STATUS_FILE"
        cp "$CLAUDE_OUTPUT" "$REVIEW_FILE"
        # Do NOT refresh snapshot — we want next sync to retry the same scope
        ;;
esac

# Refresh the incremental baseline only if the sync converged. On review or
# failure we keep the old snapshot so the next sync attempts the same diff.
if [ "$SNAPSHOT_REFRESH" -eq 1 ]; then
    refresh_snapshot
fi

# ── 5. Cleanup ────────────────────────────────────────────────────────────
: > "$MODIFIED_FILE"
rm -f "$DIFF_BUNDLE" "$PROMPT_FILE" "$CLAUDE_OUTPUT" "$PAYLOAD_FILE"

log "=== sync agent done ==="
exit 0
