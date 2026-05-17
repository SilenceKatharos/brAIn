"""JSONL append-only logs for rejections and potential duplicates."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

EXTENSION_REQUESTS = "extension_requests.jsonl"
POTENTIAL_DUPLICATES = "potential_duplicates.jsonl"


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_jsonl(root: Path, filename: str, record: dict[str, Any]) -> None:
    """Append a JSON line to ``root/filename``. Creates the file if missing."""
    record = {"ts": _utc_now(), **record}
    path = Path(root) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_extension_request(
    root: Path, kind: str, value: str, doc_id: str, detail: dict[str, Any] | None = None
) -> None:
    """Record an out-of-whitelist type that was rejected during ingestion."""
    record = {"kind": kind, "value": value, "doc_id": doc_id}
    if detail:
        record["detail"] = detail
    log_jsonl(root, EXTENSION_REQUESTS, record)


def log_potential_duplicate(
    root: Path, new_id: str, new_label: str, candidates: list[dict[str, str]], doc_id: str
) -> None:
    """Record a substring overlap between a new node and existing ones."""
    log_jsonl(
        root,
        POTENTIAL_DUPLICATES,
        {
            "new_id": new_id,
            "new_label": new_label,
            "candidates": candidates,
            "doc_id": doc_id,
        },
    )
