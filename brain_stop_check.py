#!/usr/bin/env python3
"""Stop hook: spawn the background sync agent and exit cleanly.

The previous implementation nagged Claude to update the graph itself; in
practice this got ignored on long sessions. We now offload the graph sync
to a background process (brain_sync_agent.sh) that runs while the user is
typing the next message. The Stop hook returns immediately so the foreground
session never waits on graph maintenance.
"""
import os
import subprocess
import sys
from pathlib import Path

MODIFIED_FILE = Path("/tmp/brain_session_modified.txt")
SESSION_START_FILE = Path("/tmp/brain_session_start.txt")
BRAIN_DIR = Path(__file__).resolve().parent
SYNC_AGENT = BRAIN_DIR / "brain_sync_agent.sh"


def main() -> None:
    # Skip if called from inside the sync agent (avoids recursion)
    if os.environ.get("BRAIN_HOOK_DISABLED") == "1":
        sys.exit(0)

    # Always spawn the sync agent: it uses git diff against the session-start
    # HEAD as the primary source, so it must run even when the PostToolUse
    # queue is empty (e.g. when all modifications came from Bash cp/mv/sed).
    # The agent itself decides whether there's anything to do.

    # Spawn the sync agent in the background, fully detached from this hook.
    # Pass the session's cwd as arg 1 so the sync agent knows which project
    # to sync (its per-project snapshot, HEAD baseline and status files
    # derive from this).
    if SYNC_AGENT.is_file() and os.access(SYNC_AGENT, os.X_OK):
        try:
            session_cwd = os.getcwd()
            subprocess.Popen(
                ["nohup", str(SYNC_AGENT), session_cwd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        except Exception:
            # Spawn failure must not block the user's Stop
            pass

    # Always allow the Stop — the sync runs in background
    sys.exit(0)


if __name__ == "__main__":
    main()
