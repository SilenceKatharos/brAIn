"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.db import connect, init_schema  # noqa: E402


@pytest.fixture
def conn(tmp_path: Path):
    db_path = tmp_path / "kuzu_db"
    c = connect(db_path)
    init_schema(c)
    yield c


@pytest.fixture
def log_root(tmp_path: Path) -> Path:
    return tmp_path
