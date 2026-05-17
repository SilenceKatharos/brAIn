#!/usr/bin/env python3
"""brAIn graph API — FastAPI backend for the React UI."""

from __future__ import annotations

import os
import re
import shutil
import sys
import threading
import uuid
from pathlib import Path

import kuzu
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

from lib.db import rows
from lib.query import stats as compute_stats, context_for_topic
from lib.audit import run_audit

DB_PATH = ROOT / "graph" / "kuzu_db"

app = FastAPI(title="brAIn API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _conn() -> kuzu.Connection:
    db = kuzu.Database(str(DB_PATH), read_only=True)
    return kuzu.Connection(db)


def _safe(s: str) -> str:
    """Strip characters that could break an inline Cypher string."""
    return re.sub(r"['\"\\\n\r]", "", s)[:120]


# ---------------------------------------------------------------------------
# Stats + audit
# ---------------------------------------------------------------------------

@app.get("/api/stats")
def get_stats():
    try:
        return compute_stats(_conn())
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/audit")
def get_audit():
    try:
        report = run_audit(_conn())
        return {"metrics": report.metrics, "warnings": report.warnings, "errors": report.errors}
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Full graph (for canvas rendering)
# ---------------------------------------------------------------------------

@app.get("/api/graph")
def get_graph():
    conn = _conn()
    node_rows = rows(conn.execute(
        "MATCH (n:Node) "
        "RETURN n.id, n.label, n.type, n.description, n.importance, n.sources "
        "ORDER BY n.importance DESC"
    ))
    edge_rows = rows(conn.execute(
        "MATCH (a:Node)-[r:Rel]->(b:Node) "
        "RETURN a.id AS src, b.id AS dst, r.type, r.confidence, r.evidences, r.factors, r.sources"
    ))
    return {"nodes": node_rows, "edges": edge_rows}


# ---------------------------------------------------------------------------
# Node detail
# ---------------------------------------------------------------------------

@app.get("/api/node/{node_id}")
def get_node(node_id: str, neighbors: int = 20):
    safe_id = _safe(node_id)
    result = context_for_topic(_conn(), safe_id, limit=1, neighbors=neighbors)
    if not result["matches"]:
        return {"error": f"node '{node_id}' not found"}
    return result["matches"][0]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@app.get("/api/search")
def search(
    q: str = Query(default="", max_length=100),
    node_type: str = Query(default="", max_length=40),
    limit: int = Query(default=50, le=200),
):
    conn = _conn()
    where_parts = []
    if q:
        sq = _safe(q).lower()
        where_parts.append(
            f"(toLower(n.label) CONTAINS '{sq}' OR toLower(n.description) CONTAINS '{sq}')"
        )
    if node_type:
        st = _safe(node_type)
        where_parts.append(f"n.type = '{st}'")

    where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    result = rows(conn.execute(
        f"MATCH (n:Node) {where} "
        f"RETURN n.id, n.label, n.type, n.description, n.importance "
        f"ORDER BY n.importance DESC "
        f"LIMIT {limit}"
    ))
    return result


# ---------------------------------------------------------------------------
# Types list (for filter dropdown)
# ---------------------------------------------------------------------------

@app.get("/api/types")
def get_types():
    conn = _conn()
    result = rows(conn.execute(
        "MATCH (n:Node) RETURN DISTINCT n.type AS type, count(*) AS c ORDER BY c DESC"
    ))
    return result


# ---------------------------------------------------------------------------
# Ingestion via a dedicated Claude session
# ---------------------------------------------------------------------------

_jobs: dict[str, dict] = {}

INGEST_PROMPT = """\
You are a brAIn extraction agent. Your only mission: ingest the project at {folder_path} \
(project name: {project_name}) into the brAIn causal knowledge graph.

Working directory: /home/marius/Documents/brAIn

== PROTOCOL ==
STEP 1 — Read /home/marius/Documents/brAIn/docs/SKILL.md in full. \
This defines the exact extraction protocol you must follow.

STEP 2 — List all .md files in {folder_path} that contain design or technical content. \
Skip: node_modules/, .git/, dist/, build/, site-v2/node_modules/, simulateur/

STEP 3 — For each file:
  a. Read it fully.
  b. Inventory every ## section as an explicit checklist.
  c. Extract nodes and relations following SKILL.md (section inventory → entity pass → \
relation pass → completeness+orphan review).
  d. Save the JSON payload to:
     /home/marius/Documents/brAIn/projects/{project_name}/project_{project_name}_<aspect>.json
     where <aspect> is a short snake_case descriptor of the file's theme (e.g. vision, economy).
  e. Every node and rel must have:
     - sources: ["project_{project_name}_<aspect>", "project:{project_name}"]
  f. Node IDs must match slugify(label): plain ASCII, snake_case, no parentheses/slashes/dots.
     The CLI rewrites IDs to slugify(label) — use slugify(label) directly as ID to avoid \
rel-skipping.

STEP 4 — Ingest each payload (run from /home/marius/Documents/brAIn):
  .venv/bin/python brain.py ingest projects/{project_name}/project_{project_name}_<aspect>.json

STEP 5 — After all files, verify:
  .venv/bin/python brain.py stats
  .venv/bin/python brain.py audit

STEP 6 — Gap verification (SKILL.md Step 7) for each doc_id:
  .venv/bin/python brain.py query "MATCH (n:Node) WHERE 'project_{project_name}_<aspect>' \
IN n.sources RETURN n.label, n.type ORDER BY n.importance DESC"
  Then re-read the source file section by section and identify any gaps. Re-ingest if needed.

Report a summary of what was done (nodes created, rels created, gaps found/resolved). \
Be autonomous and thorough.\
"""


def _find_claude() -> str | None:
    """Locate the claude CLI binary."""
    found = shutil.which("claude")
    if found:
        return found
    for candidate in [
        os.path.expanduser("~/.local/bin/claude"),
        "/usr/local/bin/claude",
        "/usr/bin/claude",
    ]:
        if os.path.isfile(candidate):
            return candidate
    return None


def _run_ingestion(job_id: str, project_name: str, folder_path: str) -> None:
    job = _jobs[job_id]
    claude_bin = _find_claude()

    if not claude_bin:
        job["status"] = "error"
        job["output"] = "claude CLI not found on PATH. Make sure Claude Code is installed."
        return

    # Ensure the projects/<name> directory exists
    project_dir = ROOT / "projects" / project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    prompt = INGEST_PROMPT.format(
        project_name=project_name,
        folder_path=folder_path,
    )

    cmd = [
        claude_bin,
        "--print",
        "--dangerously-skip-permissions",
        prompt,
    ]

    try:
        proc = subprocess.Popen(  # type: ignore[name-defined]
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,  # type: ignore[name-defined]
            stderr=subprocess.STDOUT,
            text=True,
            env=os.environ.copy(),
        )
        job["pid"] = proc.pid
        chunks: list[str] = []
        for line in proc.stdout:  # type: ignore[union-attr]
            chunks.append(line)
            job["output"] = "".join(chunks)
        proc.wait()
        job["exit_code"] = proc.returncode
        job["status"] = "done" if proc.returncode == 0 else "error"
    except Exception as exc:
        job["status"] = "error"
        job["output"] += f"\n\nError launching Claude: {exc}"


# Fix missing import inside the function (subprocess used above)
import subprocess  # noqa: E402  (placed after the function to keep file readable)


class IngestRequest(BaseModel):
    project_name: str
    folder_path: str


@app.post("/api/ingest")
def start_ingest(body: IngestRequest):
    project_name = re.sub(r"[^a-zA-Z0-9_\-]", "", body.project_name).strip()
    folder_path = body.folder_path.strip()

    if not project_name:
        raise HTTPException(400, "project_name must be non-empty alphanumeric/underscore")
    if not folder_path or not Path(folder_path).is_dir():
        raise HTTPException(400, f"Folder not found: {folder_path}")

    job_id = uuid.uuid4().hex[:8]
    _jobs[job_id] = {
        "status": "running",
        "output": "",
        "project_name": project_name,
        "folder_path": folder_path,
    }

    t = threading.Thread(
        target=_run_ingestion,
        args=(job_id, project_name, folder_path),
        daemon=True,
    )
    t.start()

    return {"job_id": job_id}


@app.get("/api/ingest/{job_id}")
def get_ingest_status(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    return _jobs[job_id]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
