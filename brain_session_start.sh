#!/usr/bin/env bash
# SessionStart hook: record session start time, clear leftover state,
# and inject brAIn instructions into context regardless of working directory.
date -u +%Y-%m-%dT%H:%M:%SZ > /tmp/brain_session_start.txt
rm -f /tmp/brain_session_modified.txt /tmp/brain_stop_blocked.txt

BRAIN_CLAUDE="$HOME/.claude/CLAUDE.md"
if [ -f "$BRAIN_CLAUDE" ]; then
    CONTENT=$(cat "$BRAIN_CLAUDE")
    jq -cn --arg content "$CONTENT" '{
        hookSpecificOutput: {
            hookEventName: "SessionStart",
            additionalContext: $content
        }
    }'
fi
