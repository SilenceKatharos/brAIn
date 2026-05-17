#!/usr/bin/env bash
INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // .tool_input.new_file_path // "?"')
MSG="[brAIn] ${FILE} vient d'etre modifie. Si ce changement contient une decision structurelle importante pour le projet (architecture, mecanisme causal, tradeoff de design), utilise brain_find puis brain_ingest pour mettre a jour le graphe. Ignore si detail d'implementation mineur."
jq -cn --arg msg "$MSG" '{suppressOutput:true,hookSpecificOutput:{hookEventName:"PostToolUse",additionalContext:$msg}}'
