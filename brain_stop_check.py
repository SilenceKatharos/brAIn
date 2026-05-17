#!/usr/bin/env python3
"""Stop hook: block session end if files were modified without any graph update."""
import json
import sys
from pathlib import Path

MODIFIED_FILE = Path("/tmp/brain_session_modified.txt")
SESSION_START_FILE = Path("/tmp/brain_session_start.txt")
BLOCKED_FILE = Path("/tmp/brain_stop_blocked.txt")
BRAIN_DIR = Path(__file__).resolve().parent


def cleanup():
    MODIFIED_FILE.unlink(missing_ok=True)
    SESSION_START_FILE.unlink(missing_ok=True)
    BLOCKED_FILE.unlink(missing_ok=True)


def main():
    # If already blocked once, trust Claude's judgment and allow
    if BLOCKED_FILE.exists():
        cleanup()
        sys.exit(0)

    # No files modified this session → allow
    if not MODIFIED_FILE.exists() or MODIFIED_FILE.stat().st_size == 0:
        cleanup()
        sys.exit(0)

    # Determine session start time
    if SESSION_START_FILE.exists():
        session_start = SESSION_START_FILE.read_text().strip()
    else:
        import datetime
        session_start = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Query Kuzu: any nodes updated since session start?
    sys.path.insert(0, str(BRAIN_DIR))
    try:
        from lib.db import connect, rows as db_rows

        conn = connect(BRAIN_DIR / "graph" / "kuzu_db")
        result = db_rows(
            conn.execute(
                "MATCH (n:Node) WHERE n.updated_at >= $start RETURN count(n) AS c",
                {"start": session_start},
            )
        )
        graph_updates = int(result[0]["c"]) if result else 0
    except Exception:
        cleanup()
        sys.exit(0)

    # Graph was updated this session → allow
    if graph_updates > 0:
        cleanup()
        sys.exit(0)

    # Collect modified files
    lines = MODIFIED_FILE.read_text().strip().splitlines()
    modified = sorted({ln.strip() for ln in lines if ln.strip()})[:20]
    names = [Path(f).name for f in modified]
    files_str = ", ".join(names)

    # Mark as blocked (next Stop invocation will allow unconditionally)
    BLOCKED_FILE.write_text("1")

    msg = (
        f"Fichiers modifies cette session sans mise a jour du graphe brAIn : {files_str}. "
        "Si ces changements contiennent des decisions structurelles importantes "
        "(architecture, mecanismes causaux, nouveaux composants, tradeoffs de design), "
        "utilise brain_find + brain_ingest pour mettre a jour le graphe avant de terminer. "
        "Si les modifications sont des details d'implementation mineurs sans impact structural, "
        "tu peux terminer — cette verification ne se repetira pas."
    )

    print(json.dumps({"continue": False, "stopReason": msg}))


if __name__ == "__main__":
    main()
