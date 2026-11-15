"""Agent A3 — Metrics & Insights (capability C).

Deterministic: pure SQL aggregation over the blackboard (runs / check_results /
issues) rendered to a static out/dashboard.html. No LLM, no server. Shows quality
per contract, table, rule, dimension and severity, plus the trend over the last runs.
"""

import html
from datetime import datetime, timezone

from .. import config, db

SEVERITY_WEIGHT = {"HIGH": 20, "MEDIUM": 10, "LOW": 5}
TREND_RUNS = 10


def compute_score(conn, contract_id: str):
    """(score 0-100, status) from the contract's latest completed run:
    100 minus the severity weights of the rules failing in that run."""
    row = conn.execute(
        """SELECT run_id FROM runs WHERE contract_id = ?
           AND status IN ('COMPLETED','COMPLETED_WITH_ERRORS')
           ORDER BY run_id DESC LIMIT 1""", (contract_id,)).fetchone()
    if row is None:
        return None, "NO_RUNS"
    fails = conn.execute(
        "SELECT severity, COUNT(*) FROM check_results WHERE run_id = ? AND status = 'FAIL'"
        " GROUP BY severity", (row["run_id"],)).fetchall()
    penalty = sum(SEVERITY_WEIGHT.get(r[0], 10) * r[1] for r in fails)
    score = max(0, 100 - penalty)
    status = "PASSING" if score == 100 else ("DEGRADED" if score >= 70 else "FAILING")
    return score, status


class MetricsAgent:
    name = "a3-metrics-insights"

    def render(self) -> str:
        conn = db.state()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        contracts = conn.execute(
            "SELECT * FROM contracts WHERE status='active' ORDER BY contract_id").fetchall()

        summary_rows = []
        for c in contracts:
            cid = c["contract_id"]
            run = conn.execute(
                """SELECT run_id, started_at, status, rules_executed, issues_found FROM runs
                   WHERE contract_id=? AND status LIKE 'COMPLETED%' ORDER BY run_id DESC LIMIT 1""",
                (cid,)).fetchone()
            score, status = compute_score(conn, cid)
            sev = {r[0]: r[1] for r in conn.execute(
                "SELECT severity, COUNT(*) FROM issues WHERE contract_id=? AND status='OPEN'"
                " GROUP BY severity", (cid,))}
            summary_rows.append((
                cid, c["table_name"],
                f"#{run['run_id']} {run['started_at']}" if run else "—",
                f"{run['issues_found']}/{run['rules_executed']}" if run else "—",
                sev.get("HIGH", 0), sev.get("MEDIUM", 0), sev.get("LOW", 0),
                "—" if score is None else score, status))

        trend = conn.execute(
            """SELECT contract_id, run_id, started_at, triggered_by, status, issues_found
               FROM runs ORDER BY run_id""").fetchall()
        by_contract = {}
        for t in trend:
            by_contract.setdefault(t["contract_id"], []).append(t)

        issues = conn.execute(
            """SELECT issue_id, contract_id, rule_id, dimension, severity, table_name,
                      column_name, rows_affected, occurrences, first_seen, last_seen
               FROM issues WHERE status='OPEN'
               ORDER BY severity DESC, contract_id, rule_id""").fetchall()
        conn.close()

        e = html.escape
        parts = [
            "<!doctype html><html><head><meta charset='utf-8'>",
            "<title>DQ Monitor — dashboard</title>",
            "<style>body{font-family:system-ui,sans-serif;margin:2rem;color:#111}"
            "table{border-collapse:collapse;margin:1rem 0}"
            "th,td{border:1px solid #ccc;padding:4px 10px;text-align:left;font-size:14px}"
            "th{background:#f0f0f0}.HIGH{color:#b00}.MEDIUM{color:#a60}.LOW{color:#666}"
            ".PASSING{color:#080}.DEGRADED{color:#a60}.FAILING{color:#b00}</style>",
            "</head><body>",
            f"<h1>Data Quality Monitor</h1><p>Generated {e(now)} — static snapshot from dq.db</p>",
            "<h2>Quality per data contract</h2>",
            "<table><tr><th>contract</th><th>table</th><th>last run</th><th>failing rules</th>"
            "<th>open HIGH</th><th>open MEDIUM</th><th>open LOW</th><th>score</th><th>status</th></tr>",
        ]
        for r in summary_rows:
            parts.append(
                f"<tr><td>{e(str(r[0]))}</td><td>{e(str(r[1]))}</td><td>{e(str(r[2]))}</td>"
                f"<td>{e(str(r[3]))}</td><td class='HIGH'>{r[4]}</td><td class='MEDIUM'>{r[5]}</td>"
                f"<td class='LOW'>{r[6]}</td><td>{e(str(r[7]))}</td>"
                f"<td class='{e(str(r[8]))}'>{e(str(r[8]))}</td></tr>")
        parts.append("</table>")

        parts.append("<h2>Trend — issues found per run (last %d per contract)</h2>" % TREND_RUNS)
        parts.append("<table><tr><th>contract</th>" + "".join(
            f"<th>run</th>" for _ in range(0)) + "</tr>")
        for cid, runs in by_contract.items():
            last = runs[-TREND_RUNS:]
            cells = "".join(
                f"<td>#{t['run_id']} ({e(t['triggered_by'][:4])}): {t['issues_found']} fail</td>"
                for t in last)
            parts.append(f"<tr><td>{e(cid)}</td><td colspan='{TREND_RUNS}'>{cells}</td></tr>")
        parts.append("</table>")

        parts.append("<h2>Open issues by rule / dimension / severity</h2>")
        parts.append("<table><tr><th>issue</th><th>contract</th><th>rule</th><th>dimension</th>"
                     "<th>severity</th><th>table.column</th><th>rows</th><th>occurrences</th>"
                     "<th>first seen</th><th>last seen</th></tr>")
        for i in issues:
            col = f"{i['table_name']}.{i['column_name']}" if i["column_name"] else i["table_name"]
            parts.append(
                f"<tr><td>{i['issue_id']}</td><td>{e(i['contract_id'])}</td>"
                f"<td>{e(i['rule_id'])}</td><td>{e(i['dimension'])}</td>"
                f"<td class='{e(i['severity'])}'>{e(i['severity'])}</td><td>{e(col)}</td>"
                f"<td>{i['rows_affected']}</td><td>{i['occurrences']}</td>"
                f"<td>{e(str(i['first_seen']))}</td><td>{e(str(i['last_seen']))}</td></tr>")
        parts.append("</table></body></html>")

        path = config.OUT_DIR / "dashboard.html"
        path.write_text("\n".join(parts), encoding="utf-8")
        return str(path)
