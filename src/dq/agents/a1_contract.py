"""Agent A1 — Contract Architect (capability A).

The LLM drafts an ODCS v3.0.0 contract for a source table from the introspected
schema + business context. Validation is deterministic (odcs.py); activation is
the human HITL gate (`run.py approve`). The agent itself can never activate a
contract — that is a guardrail, not a prompt hope.
"""

import json
import re

from .. import db
from .base import Agent

SYSTEM = """You are the Contract Architect agent of a contract-driven data quality monitor.

GOAL: draft one complete, valid ODCS v3.0.0 DataContract YAML for the given source table.

STEPS:
1. The task message already contains the real columns of the table (from PRAGMA table_info).
2. Design quality rules from the business context given in the task.
3. Optionally validate your draft with validate_contract_yaml — at most twice — and fix
   every reported error. Then STOP calling tools and return the final YAML.
   Do not loop: once validate_contract_yaml returns VALID (or you decide to skip it),
   produce the final answer immediately.

CONTRACT SHAPE (ODCS v3.0.0 — follow it exactly):
- top level: version "1.0.0", apiVersion v3.0.0, kind DataContract,
  id "urn:datacontract:dq-bank:<table>:1.0.0", name = <table>, status production,
  domain banking, description as an OBJECT with keys purpose/usage/limitations
  (NOT a plain string).
  ALL of these are YAML LISTS (never mappings, never scalars): tags,
  servers (one entry: server "sqlite:///data/banking.db", type custom),
  team (two objects: {username: owner email, role: owner} and
  {username: steward email, role: data steward}),
  support (one object: {channel: email, url: "mailto:..."}),
  slaProperties (list of {property, value, unit}),
  customProperties (list of {property, value}: dqScheduleMin as a number of minutes,
  dqAsOf as "2025-06-30"), contractCreatedTs.
- schema: exactly ONE object: name = <table>, physicalName = <table>,
  dataGranularityDescription, properties = one entry per column with name,
  logicalType (one of: string, date, number, integer, object, array, boolean),
  physicalType (SQLite type), and required / unique / primaryKey booleans where apt.
  FORMAT EXAMPLE — "properties" MUST be a YAML LIST of objects, never a mapping:
    schema:
      - name: customers
        physicalName: customers
        properties:
          - name: customer_id
            logicalType: integer
            physicalType: INTEGER
            primaryKey: true
            required: true
          - name: email
            logicalType: string
            physicalType: TEXT
            required: true
            unique: true
            quality:
              - name: email_not_null
                type: library
                rule: nullCount
                mustBe: 0
                unit: rows
                dimension: completeness
                severity: HIGH
                description: email must never be NULL
- quality rules: field-level rules go in the property's "quality" list; table-level
  rules go in the schema object's "quality" list. Each rule has: name (unique,
  snake_case), type ("library" or "sql"), dimension (one of accuracy, completeness,
  conformity, consistency, coverage, timeliness, uniqueness), severity (HIGH, MEDIUM
  or LOW), unit "rows", description (a string).
  - library: rule "nullCount" with mustBe 0 for NOT NULL fields;
    rule "duplicateCount" with mustBe 0 for unique fields.
  - sql: REQUIRED property "query" — a SELECT returning the offending primary keys;
    use ${table} for the table name, ${asOf} for the as-of date, ${asOfMonth} for its
    YYYY-MM. Typical queries: enum (col NOT IN (...)), format (NOT LIKE / NOT GLOB),
    range (col < 0), referential integrity (LEFT JOIN other_table ... IS NULL),
    cross-table match. FULL EXAMPLE of a valid sql rule:
      - name: status_enum
        type: sql
        dimension: conformity
        severity: HIGH
        unit: rows
        description: status must be one of the allowed values
        query: SELECT customer_id FROM ${table} WHERE status NOT IN ('active','closed','frozen')

GUARDRAILS:
- Only describe checks for the given table; never invent tables or columns.
- Never activate a contract — a human approves it afterwards.

FINAL ANSWER: the YAML document only, inside a single ```yaml fenced block."""


def draft(table: str, business_context: str, context: dict) -> str:
    # introspect the schema deterministically and hand it to the agent in the task,
    # so the LLM loop typically needs 0-2 tool calls instead of discovering everything
    conn = db.source_ro()
    cols = [{"name": r[1], "type": r[2], "notnull": bool(r[3]), "pk": bool(r[5])}
            for r in conn.execute(f'PRAGMA table_info("{table}")')]
    conn.close()

    agent = Agent("a1-contract-architect", SYSTEM,
                  ["list_tables", "get_table_schema", "validate_contract_yaml"])
    task = (
        f"Draft the ODCS v3.0.0 data contract for source table `{table}`.\n"
        f"Actual columns of `{table}`: {json.dumps(cols)}\n"
        f"Business context: {business_context}\n"
        "Write the YAML, optionally validate it once or twice with validate_contract_yaml, "
        "then return the final YAML in one ```yaml block."
    )
    out = agent.run(task, context)
    m = re.search(r"```ya?ml\s*\n(.*?)```", out, re.S | re.I)
    return m.group(1) if m else out
