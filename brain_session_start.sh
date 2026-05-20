#!/usr/bin/env bash
# SessionStart hook: per-project state initialization + brAIn context surface.
#
# For each Claude Code session, we capture the cwd's git HEAD (if any) as a
# per-project baseline file used by the sync agent. New projects get
# auto-registered as a stub in the graph so the UserPromptSubmit hook starts
# firing and the sync agent has somewhere to enrich.
#
# Multi-project safe: every state file is suffixed with the project name
# (derived from cwd basename) so two parallel sessions in different
# projects don't stomp on each other's snapshot/HEAD baselines.

date -u +%Y-%m-%dT%H:%M:%SZ > /tmp/brain_session_start.txt
rm -f /tmp/brain_session_modified.txt /tmp/brain_stop_blocked.txt

BRAIN_REPO="${BRAIN_REPO:-$(cd "$(dirname "$(realpath "$0")")" && pwd)}"
CWD="$(pwd)"
CACHE="/tmp/brain_known_projects.txt"
SNAPSHOT_ROOT="/tmp/brain_sync_baseline"

# Per-project state, only if the cwd is a git repo (universal 'this is a
# project' marker — non-git directories are out of scope).
if [ -d "$CWD/.git" ] && [ -x "$BRAIN_REPO/.venv/bin/python" ]; then
    # Project name = first slugified token of the cwd basename, matching
    # derive_project_tag's extraction so the auto-injected tag matches the
    # cache and the per-project file names.
    PROJECT_NAME="$(basename "$CWD" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9].*//')"

    if [ -n "$PROJECT_NAME" ]; then
        # Capture this project's HEAD as the sync agent's first-sync baseline.
        git -C "$CWD" rev-parse HEAD > "/tmp/brain_sync_git_head_${PROJECT_NAME}.txt" 2>/dev/null || true

        # Ensure the snapshot subdir exists (refresh_snapshot will populate
        # on first successful sync).
        mkdir -p "${SNAPSHOT_ROOT}/${PROJECT_NAME}"

        # Auto-register: if this project isn't known yet, create a stub node
        # so the UserPromptSubmit hook starts firing on the next message.
        if [ ! -f "$CACHE" ] || ! grep -qixF "$PROJECT_NAME" "$CACHE"; then
            STUB="$(mktemp /tmp/brain_autoreg.XXXXXX.json)"
            LABEL="$(basename "$CWD")"
            DATE_NOW="$(date -u +%Y-%m-%d)"
            cat > "$STUB" <<EOF
{
  "doc_id": "project_${PROJECT_NAME}",
  "nodes": [
    {
      "label": "${LABEL}",
      "type": "artifact",
      "importance": 0.5,
      "description": "Project at ${CWD}. Auto-registered by brAIn on ${DATE_NOW}. The sync agent will enrich this stub with structural content as edits accumulate; until then this node exists only to anchor the project tag."
    }
  ],
  "rels": []
}
EOF
            BRAIN_HOOK_DISABLED=1 "$BRAIN_REPO/.venv/bin/python" "$BRAIN_REPO/brain.py" \
                ingest --no-causal-check "$STUB" >/dev/null 2>&1 || true
            rm -f "$STUB"
        fi
    fi
fi

BRAIN_CLAUDE="$HOME/.claude/CLAUDE.md"
SYNC_STATUS="/tmp/brain_sync_status.txt"
SYNC_REVIEW="/tmp/brain_sync_review.txt"

CONTEXT=""
if [ -f "$BRAIN_CLAUDE" ]; then
    CONTEXT=$(cat "$BRAIN_CLAUDE")
fi

# Append the background sync agent's last status if present
if [ -f "$SYNC_STATUS" ] && [ -s "$SYNC_STATUS" ]; then
    CONTEXT="${CONTEXT}

# brAIn sync agent — last status
$(cat "$SYNC_STATUS")"
fi

# If the agent flagged something for review, surface the full output and clear it
if [ -f "$SYNC_REVIEW" ] && [ -s "$SYNC_REVIEW" ]; then
    REVIEW_CONTENT=$(cat "$SYNC_REVIEW")
    CONTEXT="${CONTEXT}

# brAIn sync agent — NEEDS REVIEW (output of last run)
$REVIEW_CONTENT

(Review this and either re-run the sync manually or correct the graph. After
reviewing, run: rm /tmp/brain_sync_review.txt)"
fi

if [ -n "$CONTEXT" ]; then
    jq -cn --arg content "$CONTEXT" '{
        hookSpecificOutput: {
            hookEventName: "SessionStart",
            additionalContext: $content
        }
    }'
fi
