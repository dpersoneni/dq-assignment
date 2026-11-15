"""Agent A5 — Issue Resolver (capability E, optional).

The LLM explores one issue (read-only tools only) and returns a STRUCTURED suggestion:
{root_cause, suggested_fix, confidence, proposed_action, proposed_sql}.
Guardrails: it may only reference the rule that fired; its SQL tool is read-only; a
proposed write-back is only a proposal — the human decides via ask_human and only the
orchestrator may execute it, in a transaction, after approval (ADR-07). If the LLM is
unavailable, a deterministic template suggestion is used instead (disclosed).
"""

import json
import re

from .. import config
from .base import Agent

SYSTEM = """You are the Issue Resolver agent (capability E) of a contract-driven data
quality monitor. A steward asked you to explore ONE data quality issue and propose what
to do about it.

STEPS:
1. query_state: read the issue row, its latest check_results (sample of offending keys,
   executed_sql), and the contract rule that fired.
2. run_sql_readonly: inspect the offending rows and the related/parent tables to form a
   root-cause hypothesis (e.g. for an orphan FK, check whether the parent key exists).

RULES:
- Reference ONLY the rule that fired (issue.rule_id). Never invent new rules.
- proposed_action must be one of: no_data_change | flag_for_human | propose_write_back.
- propose_write_back is allowed ONLY with a single UPDATE/DELETE/INSERT statement in
  proposed_sql against the source table, and only when confidence is high.
- If unsure or confidence is low, proposed_action MUST be flag_for_human.
- You can never execute writes yourself: a human approves, the orchestrator executes.

FINAL ANSWER: only one JSON object (no prose), e.g.
{"root_cause": "...", "suggested_fix": "...", "confidence": "high",
 "proposed_action": "propose_write_back", "proposed_sql": "UPDATE ..."}"""

ACTIONS = ("no_data_change", "flag_for_human", "propose_write_back")
_WRITE_SQL = re.compile(r"^\s*(UPDATE|DELETE|INSERT)\b", re.I)


def _parse_suggestion(text: str) -> dict:
    m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S | re.I)
    raw = m.group(1) if m else text
    s = json.loads(raw)
    if s.get("proposed_action") not in ACTIONS:
        s["proposed_action"] = "flag_for_human"
    if s["proposed_action"] != "propose_write_back":
        s["proposed_sql"] = None
    elif not s.get("proposed_sql") or not _WRITE_SQL.match(s["proposed_sql"]):
        s["proposed_action"] = "flag_for_human"
        s["proposed_sql"] = None
    s.setdefault("confidence", "low")
    s.setdefault("root_cause", "")
    s.setdefault("suggested_fix", "")
    return s


class IssueResolver:
    name = "a5-issue-resolver"

    def suggest(self, issue_id: int, context: dict) -> dict:
        agent = Agent(self.name, SYSTEM, ["query_state", "run_sql_readonly"])
        task = (
            f"Explore issue #{issue_id}.\n"
            f"1) query_state: SELECT * FROM issues WHERE issue_id = {issue_id}; "
            f"SELECT * FROM check_results WHERE rule_id = (SELECT rule_id FROM issues WHERE issue_id = {issue_id}) "
            f"ORDER BY result_id DESC LIMIT 1; and the contract row for that issue's contract_id.\n"
            "2) run_sql_readonly: inspect the offending rows (the sample keys from check_results) "
            "and related tables to hypothesize the root cause.\n"
            "3) Return ONLY the JSON suggestion object."
        )
        try:
            out = agent.run(task, context)
            return _parse_suggestion(out)
        except Exception as e:
            # deterministic fallback so capability E degrades gracefully without the LLM
            return {"root_cause": f"LLM unavailable ({type(e).__name__}: {e}); see check_results sample",
                    "suggested_fix": "Inspect the sample offending keys from the latest run manually.",
                    "confidence": "low", "proposed_action": "flag_for_human",
                    "proposed_sql": None, "fallback": True}
