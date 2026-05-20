#!/usr/bin/env bash
# PostToolUse hook: silently track Edit/Write file paths for the sync agent's
# fallback queue. The actual graph maintenance is performed by the background
# sync agent (brain_sync_agent.sh) at Stop — NOT by the working session.
# This hook used to nag Claude to ingest the change inline; that's now wrong
# (it would double up with the sync agent and bypass the strict workflow).

# Skip when called from inside the sync agent itself (prevents recursion).
[ "${BRAIN_HOOK_DISABLED:-0}" = "1" ] && exit 0

INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // .tool_input.new_file_path // ""')

# Append modified file to the sync agent's fallback queue. The agent prefers
# its snapshot/git diff sources but uses this queue when neither is available.
if [ -n "$FILE" ] && [ "$FILE" != "null" ]; then
    echo "$FILE" >> /tmp/brain_session_modified.txt
fi

# No additionalContext — the sync agent handles everything; Claude doesn't
# need a per-edit reminder, that's what the UserPromptSubmit hook is for.
echo '{"suppressOutput":true}'
