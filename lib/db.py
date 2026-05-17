"""Kuzu connection and schema initialization."""

from __future__ import annotations

from pathlib import Path

import kuzu

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "graph" / "kuzu_db"

NODE_DDL = """
CREATE NODE TABLE IF NOT EXISTS Node(
    id          STRING,
    label       STRING,
    type        STRING,
    description STRING,
    importance  DOUBLE DEFAULT 0.5,
    created_at  STRING,
    updated_at  STRING,
    sources     STRING[],
    PRIMARY KEY (id)
)
"""

REL_DDL = """
CREATE REL TABLE IF NOT EXISTS Rel(
    FROM Node TO Node,
    type        STRING,
    confidence  DOUBLE DEFAULT 0.8,
    evidences   STRING[],
    factors     STRING[],
    sources     STRING[],
    created_at  STRING,
    updated_at  STRING
)
"""


def connect(db_path: Path | str | None = None) -> kuzu.Connection:
    """Open or create a Kuzu database and return a connection."""
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    db = kuzu.Database(str(path))
    return kuzu.Connection(db)


def init_schema(conn: kuzu.Connection) -> None:
    """Idempotent schema setup."""
    conn.execute(NODE_DDL)
    conn.execute(REL_DDL)


def rows(result) -> list[dict]:
    """Materialize a Kuzu QueryResult as a list of dicts keyed by column name."""
    cols = result.get_column_names()
    out = []
    while result.has_next():
        record = result.get_next()
        out.append(dict(zip(cols, record)))
    return out


def one(result) -> dict | None:
    """Return the first row as a dict, or None if empty."""
    cols = result.get_column_names()
    if result.has_next():
        return dict(zip(cols, result.get_next()))
    return None
