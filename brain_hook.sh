#!/usr/bin/env bash
INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // .tool_input.new_file_path // ""')

# Log modified file for Stop hook consistency check
if [ -n "$FILE" ] && [ "$FILE" != "null" ]; then
    echo "$FILE" >> /tmp/brain_session_modified.txt
fi

DISPLAY_FILE="${FILE:-?}"
MSG="[brAIn] ${DISPLAY_FILE} vient d'etre modifie. Mets a jour le graphe : si le changement est structurel (architecture, mecanisme, tradeoff), cree ou enrichis un noeud dedie. Si c'est un detail mineur (UX, renommage, style), mets quand meme a jour la description du noeud existant le plus proche. Ne pas ignorer."
jq -cn --arg msg "$MSG" '{suppressOutput:true,hookSpecificOutput:{hookEventName:"PostToolUse",additionalContext:$msg}}'
