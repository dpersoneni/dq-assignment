"""Agent A2 — Quality Executor (capability B).

Deterministic by design (ADR-06): loads an ACTIVE contract, compiles its quality
rules to SQL (rules.py), executes them against the source opened READ-ONLY, and
writes runs / check_results / issues to the blackboard (history + dedup included).
No LLM is used or imported here. Only contracts with status='active' (human-approved)
are executed.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone

from .. import db, odcs, rules


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class QualityExecutor:
    name = "a2-quality-executor"

    def run_contract(self, contract_id: str, triggered_by: str = "manual") -> int:
        conn = db.state()
        row = conn.execute("SELECT * FROM contracts WHERE contract_id = ?",
                           (contract_id,)).fetchone()
        if row is None:
            print(f"[A2] unknown contract '{contract_id}' — run `python run.py sync` first")
            return 1
        if row["status"] != "active":
            self._log(conn, None, contract_id, "run_contract", "skipped",
                      f"status={row['status']} (only active contracts run)")
            conn.commit()
            print(f"[A2] {contract_id}: skipped (status={row['status']})")
            return 0
        if conn.execute("SELECT 1 FROM runs WHERE contract_id = ? AND status = 'RUNNING'",
                        (contract_id,)).fetchone():
            self._log(conn, None, contract_id, "run_contract", "skipped",
                      "another run is already RUNNING for this contract")
            conn.commit()
            print(f"[A2] {contract_id}: skipped (run already in progress)")
            return 0

        try:
            contract, errors = odcs.validate_file(row["file_path"])
            if errors:
                raise ValueError("invalid contract: " + "; ".join(errors))
            compiled = rules.compile_contract(contract)
        except Exception as e:
            run_id = self._start_run(conn, contract_id, triggered_by)
            conn.execute("UPDATE runs SET status='FAILED', finished_at=?, error=? WHERE run_id=?",
                         (_now(), str(e), run_id))
            conn.commit()
            self._log(conn, run_id, contract_id, "run_contract", "error", str(e))
            conn.commit()
            print(f"[A2] {contract_id}: FAILED before execution: {e}")
            return 1

        run_id = self._start_run(conn, contract_id, triggered_by)
        self._log(conn, run_id, contract_id, "run_contract", "ok",
                  f"started ({triggered_by}), {len(compiled)} rules compiled from contract")
        conn.commit()

        src = db.source_ro()
        executed = errored = failing = 0
        try:
            for r in compiled:
                try:
                    n = src.execute(f'SELECT COUNT(*) FROM ({r["sql"]})').fetchone()[0]
                    sample = [list(x) for x in src.execute(f'SELECT * FROM ({r["sql"]}) LIMIT 5')]
                    status = "PASS" if n == 0 else "FAIL"
                    conn.execute(
                        """INSERT INTO check_results
                           (run_id, rule_id, table_name, column_name, dimension, severity,
                            status, rows_affected, sample, executed_sql, checked_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (run_id, r["rule_id"], r["table"], r["column"], r["dimension"],
                         r["severity"], status, n, json.dumps(sample), r["sql"], _now()))
                    executed += 1
                    if status == "FAIL":
                        failing += 1
                        self._upsert_issue(conn, contract_id, r, n)
                except Exception as e:
                    errored += 1
                    conn.execute(
                        """INSERT INTO check_results
                           (run_id, rule_id, table_name, column_name, dimension, severity,
                            status, rows_affected, executed_sql, error, checked_at)
                           VALUES (?,?,?,?,?,?, 'ERROR', 0, ?, ?, ?)""",
                        (run_id, r["rule_id"], r["table"], r["column"], r["dimension"],
                         r["severity"], r["sql"], str(e), _now()))
                    self._log(conn, run_id, contract_id, f"rule:{r['rule_id']}", "error", str(e))
        finally:
            src.close()

        status = "COMPLETED" if errored == 0 else "COMPLETED_WITH_ERRORS"
        conn.execute(
            """UPDATE runs SET status=?, finished_at=?, rules_executed=?, rules_errored=?,
               issues_found=? WHERE run_id=?""",
            (status, _now(), executed, errored, failing, run_id))
        if row["schedule_min"]:
            nxt = (datetime.now(timezone.utc)
                   + timedelta(minutes=row["schedule_min"])).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("UPDATE contracts SET next_run_due=? WHERE contract_id=?",
                         (nxt, contract_id))
        conn.commit()
        self._log(conn, run_id, contract_id, "run_contract", "ok",
                  f"{status}: {executed} rules executed, {failing} failing, {errored} errors")
        conn.commit()
        conn.close()
        print(f"[A2] {contract_id}: run #{run_id} {status} — {executed} rules, "
              f"{failing} failing, {errored} errors")
        return 0

    def _start_run(self, conn, contract_id, triggered_by) -> int:
        cur = conn.execute(
            "INSERT INTO runs (contract_id, triggered_by, started_at, status) VALUES (?,?,?, 'RUNNING')",
            (contract_id, triggered_by, _now()))
        conn.commit()
        return cur.lastrowid

    def _upsert_issue(self, conn, contract_id, rule, rows_affected) -> None:
        """Dedup: one OPEN issue per (contract, rule); repeats bump occurrences."""
        fp = hashlib.sha1(f"{contract_id}:{rule['rule_id']}".encode()).hexdigest()[:16]
        now = _now()
        existing = conn.execute(
            "SELECT issue_id FROM issues WHERE fingerprint = ? AND status = 'OPEN'",
            (fp,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE issues SET last_seen=?, occurrences=occurrences+1, rows_affected=? WHERE issue_id=?",
                (now, rows_affected, existing["issue_id"]))
        else:
            conn.execute(
                """INSERT INTO issues
                   (contract_id, rule_id, fingerprint, status, severity, dimension,
                    table_name, column_name, rows_affected, first_seen, last_seen, occurrences)
                   VALUES (?,?,?,'OPEN',?,?,?,?,?,?,?,1)""",
                (contract_id, rule["rule_id"], fp, rule["severity"], rule["dimension"],
                 rule["table"], rule["column"], rows_affected, now, now))

    def _log(self, conn, run_id, contract_id, action, status, detail) -> None:
        conn.execute(
            "INSERT INTO steps (run_id, contract_id, agent, action, status, detail, at)"
            " VALUES (?,?,?,?,?,?,?)",
            (run_id, contract_id, self.name, action, status, detail, _now()))
