#!/usr/bin/env bash
# SessionStart hook: record session start time and clear leftover state
date -u +%Y-%m-%dT%H:%M:%SZ > /tmp/brain_session_start.txt
rm -f /tmp/brain_session_modified.txt /tmp/brain_stop_blocked.txt
