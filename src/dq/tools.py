"""Tool registry for LLM agents.

Each tool has a JSON-schema definition (sent to the Mistral API) and a guardrailed
executor. Guardrails live HERE in code, not in prompts: read-only SQL only, HITL
blocking, no unknown tools.
"""

import json
import re

import yaml

from . import db, odcs

_READONLY = re.compile(r"^\s*(SELECT|WITH)\b", re.I)


def _fn(name, description, properties, required):
    return {"type": "function",
            "function": {"name": name, "description": description,
                         "parameters": {"type": "object", "properties": properties,
                                        "required": required}}}


DEFINITIONS = {
    "list_tables": _fn(
        "list_tables", "List all tables in the source database.", {}, []),
    "get_table_schema": _fn(
        "get_table_schema",
        "Get the columns (name, type, notnull, pk) of a source table.",
        {"table": {"type": "string"}}, ["table"]),
    "validate_contract_yaml": _fn(
        "validate_contract_yaml",
        "Validate a proposed ODCS v3.0.0 contract YAML against the ODCS JSON schema "
        "and the source database. Returns 'VALID' or a list of errors to fix.",
        {"table": {"type": "string"}, "yaml_text": {"type": "string"}},
        ["table", "yaml_text"]),
    "run_sql_readonly": _fn(
        "run_sql_readonly",
        "Run a single read-only SELECT/WITH query against the SOURCE database. "
        "At most 50 rows are returned.",
        {"sql": {"type": "string"}}, ["sql"]),
    "query_state": _fn(
        "query_state",
        "Run a single read-only SELECT/WITH query against the product state DB (dq.db): "
        "tables contracts, runs, check_results, issues, issue_suggestions, notifications, "
        "marketplace_entries, steps, llm_calls.",
        {"sql": {"type": "string"}}, ["sql"]),
    "ask_human": _fn(
        "ask_human",
        "HITL gate: ask the human a question and return their choice. Blocks until "
        "answered, unless the run was started with --auto-approve.",
        {"question": {"type": "string"},
         "options": {"type": "array", "items": {"type": "string"}}},
        ["question", "options"]),
}


def _ask_human(args, context):
    question = args.get("question", "")
    options = args.get("options") or ["approve", "reject"]
    if context.get("auto_approve"):
        print(f"[HITL auto-approve] {question} -> {options[0]}")
        return options[0]
    print(f"\n[HITL gate] {question}")
    for i, o in enumerate(options, 1):
        print(f"  {i}) {o}")
    while True:
        sel = input("your choice> ").strip()
        if sel.isdigit() and 1 <= int(sel) <= len(options):
            return options[int(sel) - 1]
        if sel in options:
            return sel
        print("invalid choice, try again")


def execute(name, args, context):
    """Execute one tool call; returns a string result for the LLM. All guardrails here."""
    try:
        if name == "list_tables":
            conn = db.source_ro()
            rows = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            conn.close()
            return json.dumps(rows)

        if name == "get_table_schema":
            conn = db.source_ro()
            cols = [{"name": r[1], "type": r[2], "notnull": bool(r[3]), "pk": bool(r[5])}
                    for r in conn.execute(f"PRAGMA table_info({args['table']})")]
            conn.close()
            return json.dumps(cols)

        if name == "validate_contract_yaml":
            contract = odcs.normalize_contract(yaml.safe_load(args["yaml_text"]))
            conn = db.source_ro()
            tables = odcs._source_tables(conn)
            conn.close()
            errors = odcs.validate_contract(contract, tables)
            return "VALID" if not errors else "ERRORS:\n" + "\n".join("- " + e for e in errors)

        if name == "run_sql_readonly":
            sql = args["sql"]
            if not _READONLY.match(sql):
                return "REJECTED: only a single read-only SELECT/WITH statement is allowed"
            conn = db.source_ro()
            cur = conn.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(50)
            conn.close()
            return json.dumps({"columns": cols, "rows": [list(r) for r in rows],
                               "truncated": len(rows) == 50})

        if name == "query_state":
            sql = args["sql"]
            if not _READONLY.match(sql):
                return "REJECTED: only a single read-only SELECT/WITH statement is allowed"
            conn = db.state()
            cur = conn.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(50)
            conn.close()
            return json.dumps({"columns": cols, "rows": [list(r) for r in rows]})

        if name == "ask_human":
            return _ask_human(args, context)

        return f"unknown tool: {name}"
    except Exception as e:
        return f"tool error: {type(e).__name__}: {e}"
