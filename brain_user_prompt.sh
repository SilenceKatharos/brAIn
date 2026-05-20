#!/usr/bin/env bash
# brain_user_prompt.sh — UserPromptSubmit hook
#
# Injected as additionalContext on every user message: a short reminder of
# the graph-first protocol whenever cwd is under a brain-tracked project.
#
# Detection is automatic: the brain graph is the authority on which projects
# exist. /tmp/brain_known_projects.txt holds the list of project tags as
# refreshed by brain.py ingest on every successful run. New project added to
# the graph → next ingest writes the new name to the cache → this hook
# picks it up at the next user message. No hardcoded names anywhere.

[ "${BRAIN_HOOK_DISABLED:-0}" = "1" ] && exit 0

CACHE="/tmp/brain_known_projects.txt"
BRAIN_DIR="${BRAIN_DIR:-$(cd "$(dirname "$(realpath "$0")")" && pwd)}"

# If the cache is missing or empty, compute it once via the CLI (slow path,
# runs maybe once per machine boot). Subsequent invocations hit the file.
if [ ! -s "$CACHE" ] && [ -x "$BRAIN_DIR/.venv/bin/python" ]; then
    "$BRAIN_DIR/.venv/bin/python" "$BRAIN_DIR/brain.py" query \
        "MATCH (n:Node) UNWIND n.sources AS s WITH s WHERE s STARTS WITH 'project:' RETURN DISTINCT s" \
        2>/dev/null | jq -r '.[]."s"' 2>/dev/null | sed 's/^project://' > "$CACHE.tmp" \
        && mv "$CACHE.tmp" "$CACHE"
fi

# No cache available → exit silent (hook never blocks, never adds noise on
# a fresh machine).
[ ! -s "$CACHE" ] && exit 0

# Match the cwd's path segments against known projects.
# Deepest match wins, so a nested cwd like brAIn/projects/choros/ resolves
# to "choros" rather than the parent "brain".
CWD_LOWER="$(pwd | tr '[:upper:]' '[:lower:]')"
PROJECT=""
# Walk path segments from deepest to shallowest
DIR="$CWD_LOWER"
while [ "$DIR" != "/" ] && [ -n "$DIR" ]; do
    SEG="$(basename "$DIR")"
    if grep -qixF "$SEG" "$CACHE"; then
        PROJECT="$SEG"
        break
    fi
    DIR="$(dirname "$DIR")"
done

# Outside any tracked project → no reminder.
[ -z "$PROJECT" ] && exit 0

MSG="[brAIn] Active project: ${PROJECT}. QUERY the graph for context (brain_find / brain_show / brain_causes / brain_effects / brain_paths) before Read on 'why / how / what-relates-to / tradeoff' questions. DO NOT call brain_ingest or run brain ingest yourself — graph maintenance is the background sync agent's job, fired automatically at your Stop. Read files for exact lines / current syntax / post-edit verification."

jq -cn --arg msg "$MSG" '{
    hookSpecificOutput: {
        hookEventName: "UserPromptSubmit",
        additionalContext: $msg
    }
}'
