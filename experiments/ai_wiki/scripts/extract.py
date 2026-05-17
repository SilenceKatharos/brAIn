#!/usr/bin/env python3
"""Agentic extraction of documents into ingest-ready JSON.

Claude has access to a query_graph tool it calls before creating any node.
Retrieval is driven by the model's own needs, not by a heuristic preflight pass.

The graph is long-term memory; the context window is working memory.
Only the neighborhood of the concept currently being considered is loaded.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

import anthropic

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.db import connect, init_schema  # noqa: E402
from lib.query import context_for_topic  # noqa: E402

API_KEY_FILE = PROJECT_ROOT / "Important" / "API.txt"
SKILL_PATH = PROJECT_ROOT / "docs" / "SKILL.md"

MODEL = "claude-sonnet-4-6"
MAX_PARSE_RETRIES = 3

PRICE_INPUT_PER_M = 3.00
PRICE_OUTPUT_PER_M = 15.00
PRICE_CACHE_READ_PER_M = 0.30
PRICE_CACHE_WRITE_PER_M = 3.75

TOOLS = [
    {
        "name": "query_graph",
        "description": (
            "Search the knowledge graph for nodes matching a concept. "
            "Call this BEFORE creating any node to check if an equivalent already exists. "
            "If a match is found with the same or equivalent semantics, reuse its `id` exactly. "
            "Returns matching nodes with their 1-hop neighborhood."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "The concept, entity, or term to search for.",
                }
            },
            "required": ["topic"],
        },
    }
]

EXTRACTION_USER_PREAMBLE = (
    "Extract this document into the ingest-format JSON described in the system prompt.\n\n"
    "CRITICAL — descriptions must be comprehensive: capture mechanisms, historical context, "
    "variants, quantitative facts, examples, and how the concept works. "
    "A reader with only the graph should lose no useful information. "
    "One vague sentence is not acceptable.\n\n"
    "CRITICAL — evidence on each relation must explain WHY the relationship holds: "
    "cite the mechanism, the condition, or the reasoning — not just label the link.\n\n"
    "Use query_graph before creating each node to check if it already exists and reuse its id. "
    "When done, return ONLY the final JSON object — no prose, no code fence.\n\n"
    "Document:\n\n"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_api_key() -> str:
    if not API_KEY_FILE.exists():
        sys.exit(f"missing API key file: {API_KEY_FILE}")
    key = API_KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        sys.exit(f"API key file is empty: {API_KEY_FILE}")
    return key


def _cost_usd(usage: dict) -> float:
    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0) or 0
    cache_write = usage.get("cache_creation_input_tokens", 0) or 0
    plain_input = max(inp - cache_read - cache_write, 0)
    return (
        plain_input * PRICE_INPUT_PER_M / 1_000_000
        + out * PRICE_OUTPUT_PER_M / 1_000_000
        + cache_read * PRICE_CACHE_READ_PER_M / 1_000_000
        + cache_write * PRICE_CACHE_WRITE_PER_M / 1_000_000
    )


def _usage_to_dict(usage) -> dict:
    d = {
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
    }
    cr = getattr(usage, "cache_read_input_tokens", None)
    cw = getattr(usage, "cache_creation_input_tokens", None)
    if cr is not None:
        d["cache_read_input_tokens"] = cr
    if cw is not None:
        d["cache_creation_input_tokens"] = cw
    return d


def _strip_fence(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*\n(.*?)\n```$", text, flags=re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return text


def _parse_extraction(text: str, doc_id: str) -> dict:
    cleaned = _strip_fence(text)
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("extraction must be a JSON object")
    if "nodes" not in data or "rels" not in data:
        raise ValueError("extraction missing 'nodes' or 'rels' keys")
    if not isinstance(data["nodes"], list) or not isinstance(data["rels"], list):
        raise ValueError("'nodes' and 'rels' must be lists")
    data.setdefault("doc_id", doc_id)
    return data


def _system_blocks(skill_text: str) -> list[dict]:
    return [
        {
            "type": "text",
            "text": skill_text,
            "cache_control": {"type": "ephemeral"},
        }
    ]


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def _execute_tool(name: str, tool_input: dict, conn) -> str:
    if name == "query_graph":
        topic = tool_input.get("topic", "")
        result = context_for_topic(conn, topic, limit=10, neighbors=5)
        return json.dumps(result, ensure_ascii=False, indent=2)
    return json.dumps({"error": f"unknown tool: {name}"})


# ---------------------------------------------------------------------------
# Agentic extraction loop
# ---------------------------------------------------------------------------

def _call_extract_agentic(
    client,
    skill_text: str,
    doc_text: str,
    doc_id: str,
    conn,
) -> tuple[dict, dict]:
    """Run the agentic extraction loop.

    Claude calls query_graph as many times as needed, then emits the final JSON.
    Returns (parsed_payload, info_dict).
    """
    messages: list[dict] = [
        {"role": "user", "content": EXTRACTION_USER_PREAMBLE + doc_text}
    ]
    usage_total = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    tool_calls = 0
    parse_attempts = 0

    while True:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=16384,
            system=_system_blocks(skill_text),
            tools=TOOLS,
            messages=messages,
        )

        u = _usage_to_dict(resp.usage)
        for k in usage_total:
            usage_total[k] += u.get(k, 0)

        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason == "tool_use":
            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    result_text = _execute_tool(block.name, block.input, conn)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                        }
                    )
                    tool_calls += 1
            messages.append({"role": "user", "content": tool_results})
            continue

        if resp.stop_reason == "end_turn":
            text_blocks = [b for b in resp.content if hasattr(b, "text")]
            if not text_blocks:
                raise RuntimeError("no text block in final response")
            text = text_blocks[-1].text
            try:
                parsed = _parse_extraction(text, doc_id)
                return parsed, {"usage": usage_total, "tool_calls": tool_calls}
            except (json.JSONDecodeError, ValueError) as exc:
                parse_attempts += 1
                if parse_attempts >= MAX_PARSE_RETRIES:
                    raise RuntimeError(
                        f"extraction failed after {MAX_PARSE_RETRIES} parse attempts: {exc}"
                    )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Your previous response failed to parse: {exc}. "
                            "Please return ONLY the corrected JSON object, no prose, no code fence."
                        ),
                    }
                )
                continue

        raise RuntimeError(f"unexpected stop_reason: {resp.stop_reason}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract_one(
    client,
    skill_text: str,
    doc_path: Path,
    doc_id: str,
    out_path: Path,
    conn,
    log_path: Path,
) -> dict:
    """Extract a single document and persist the JSON. Returns the log record."""
    t0 = time.perf_counter()
    doc_text = doc_path.read_text(encoding="utf-8")

    log_record: dict = {
        "ts": _utc_now(),
        "doc_id": doc_id,
        "doc_path": str(doc_path),
        "model": MODEL,
        "doc_bytes": len(doc_text),
        "extraction": {},
        "total_cost_usd": 0.0,
        "duration_s": 0.0,
        "tool_calls": 0,
        "success": False,
        "error": None,
        "result": None,
    }

    try:
        parsed, ex_info = _call_extract_agentic(client, skill_text, doc_text, doc_id, conn)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        cost = _cost_usd(ex_info["usage"])
        log_record["extraction"] = {"usage": ex_info["usage"], "cost_usd": cost}
        log_record["tool_calls"] = ex_info["tool_calls"]
        log_record["total_cost_usd"] = round(cost, 6)
        log_record["success"] = True
        log_record["result"] = {
            "node_count": len(parsed.get("nodes", [])),
            "rel_count": len(parsed.get("rels", [])),
            "json_bytes": out_path.stat().st_size,
            "out_path": str(out_path),
        }
    except Exception as exc:
        log_record["error"] = repr(exc)

    log_record["duration_s"] = round(time.perf_counter() - t0, 3)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(log_record, ensure_ascii=False) + "\n")

    return log_record


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Agentic extraction — tool use mode.")
    p.add_argument("--corpus-dir", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--log-file", type=Path, default=None)
    p.add_argument("--db", type=Path, default=PROJECT_ROOT / "graph" / "kuzu_db")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--ext", default=".md")
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    api_key = load_api_key()
    client = anthropic.Anthropic(api_key=api_key)
    skill_text = SKILL_PATH.read_text(encoding="utf-8")
    conn = connect(args.db)
    init_schema(conn)

    docs = sorted(args.corpus_dir.glob(f"*{args.ext}"))
    if args.limit > 0:
        docs = docs[: args.limit]
    if not docs:
        sys.exit(f"no documents found in {args.corpus_dir}")

    log_path = args.log_file or (args.out_dir / "extraction_logs.jsonl")
    print(f"model={MODEL}  docs={len(docs)}  db={args.db}\n")

    total_cost = 0.0
    total_tool_calls = 0

    for i, doc_path in enumerate(docs, 1):
        doc_id = doc_path.stem
        out_path = args.out_dir / f"{doc_id}.json"
        if out_path.exists() and not args.force:
            print(f"  [{i:>2}/{len(docs)}] {doc_id}  skipped")
            continue

        record = extract_one(
            client=client,
            skill_text=skill_text,
            doc_path=doc_path,
            doc_id=doc_id,
            out_path=out_path,
            conn=conn,
            log_path=log_path,
        )
        total_cost += record["total_cost_usd"]
        total_tool_calls += record.get("tool_calls", 0)
        if record["success"]:
            n = record["result"]["node_count"]
            r = record["result"]["rel_count"]
            tc = record["tool_calls"]
            print(
                f"  [{i:>2}/{len(docs)}] {doc_id}  ok  "
                f"nodes={n}  rels={r}  tool_calls={tc}  "
                f"${record['total_cost_usd']:.4f}  {record['duration_s']:.1f}s"
            )
        else:
            print(f"  [{i:>2}/{len(docs)}] {doc_id}  FAILED  {record['error']}")

    print(f"\nDone. Total cost ${total_cost:.4f}. Total tool calls {total_tool_calls}.")


if __name__ == "__main__":
    main()
