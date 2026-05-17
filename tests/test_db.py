"""Database connection and schema initialization."""

from lib.db import connect, init_schema, rows


def test_init_idempotent(tmp_path):
    db_path = tmp_path / "db"
    c = connect(db_path)
    init_schema(c)
    init_schema(c)  # second call must not fail


def test_schema_tables_exist(conn):
    # CALL show_tables() returns the registered tables
    result = rows(conn.execute("CALL show_tables() RETURN *"))
    names = {row["name"] for row in result}
    assert "Node" in names
    assert "Rel" in names


def test_empty_db_returns_zero(conn):
    n = rows(conn.execute("MATCH (n:Node) RETURN count(*) AS c"))[0]["c"]
    r = rows(conn.execute("MATCH ()-[r:Rel]->() RETURN count(*) AS c"))[0]["c"]
    assert n == 0
    assert r == 0
