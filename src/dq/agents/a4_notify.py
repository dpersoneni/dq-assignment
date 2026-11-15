"""Agent A4 — Notify & Marketplace (capability D).

Deterministic templates, no LLM. Guardrail: notification recipients are read ONLY
from the active contract's `team` section (owner / data steward) — the agent cannot
misroute. Channel is simulated (permitted by the brief): one JSON file per issue in
out/notifications/ + a row in `notifications`. Marketplace: per-contract quality
score/status in out/marketplace.json + `marketplace_entries` table.
"""

import json
from datetime import datetime, timezone

from .. import config, db, odcs
from .a3_metrics import compute_score


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _rule_description(contract: dict, rule_id: str) -> str:
    obj = contract["schema"][0]
    for q in obj.get("quality", []) or []:
        if q.get("name") == rule_id:
            return q.get("description", "")
    for p in obj.get("properties", []):
        for q in p.get("quality", []) or []:
            if q.get("name") == rule_id:
                return q.get("description", "")
    return ""


class NotifyMarketplaceAgent:
    name = "a4-notify-marketplace"

    def publish(self) -> dict:
        conn = db.state()
        notif_dir = config.OUT_DIR / "notifications"
        notif_dir.mkdir(parents=True, exist_ok=True)

        products = []
        sent = 0
        for c in conn.execute(
                "SELECT * FROM contracts WHERE status='active' ORDER BY contract_id"):
            cid = c["contract_id"]
            contract, _ = odcs.validate_file(c["file_path"])
            team = {t.get("role", ""): t.get("username", "") for t in contract.get("team", [])}
            owner = team.get("owner", "unknown")
            steward = team.get("data steward", team.get("steward", "unknown"))

            run = conn.execute(
                """SELECT run_id FROM runs WHERE contract_id=? AND status LIKE 'COMPLETED%'
                   ORDER BY run_id DESC LIMIT 1""", (cid,)).fetchone()
            score, status = compute_score(conn, cid)
            open_issues = conn.execute(
                "SELECT * FROM issues WHERE contract_id=? AND status='OPEN' ORDER BY issue_id",
                (cid,)).fetchall() if run else []

            for issue in open_issues:
                already = conn.execute(
                    "SELECT 1 FROM notifications WHERE issue_id = ?",
                    (issue["issue_id"],)).fetchone()
                if already:
                    continue
                sample_row = conn.execute(
                    """SELECT sample FROM check_results WHERE run_id=? AND rule_id=?
                       ORDER BY result_id DESC LIMIT 1""",
                    (run["run_id"], issue["rule_id"])).fetchone()
                sample = json.loads(sample_row["sample"]) if sample_row and sample_row["sample"] else []
                desc = _rule_description(contract, issue["rule_id"])
                col = f"{issue['table_name']}.{issue['column_name']}" if issue["column_name"] else issue["table_name"]
                subject = (f"[DQ][{issue['severity']}] {cid}: rule '{issue['rule_id']}' failing "
                           f"({issue['rows_affected']} rows)")
                body = (
                    f"Data contract '{cid}' (table {issue['table_name']}) rule '{issue['rule_id']}' "
                    f"[{issue['dimension']}/{issue['severity']}] is failing: {issue['rows_affected']} row(s) "
                    f"affected, seen {issue['occurrences']}x since {issue['first_seen']}.\n"
                    f"Check: {desc}\nField: {col}\nSample offending keys: {sample}\n"
                    f"Explore/fix interactively: python run.py resolve --issue {issue['issue_id']}")
                payload = {
                    "channel": "file (simulated email)", "sent_at": _now(),
                    "to": {"owner": owner, "steward": steward},
                    "subject": subject, "body": body,
                    "issue": {k: issue[k] for k in
                              ("issue_id", "contract_id", "rule_id", "dimension", "severity",
                               "table_name", "column_name", "rows_affected", "occurrences")},
                    "sample_offending_keys": sample,
                }
                path = notif_dir / f"issue_{issue['issue_id']}.json"
                path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                conn.execute(
                    """INSERT INTO notifications
                       (issue_id, run_id, channel, to_owner, to_steward, subject, body,
                        payload_path, sent_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (issue["issue_id"], run["run_id"], "file", owner, steward,
                     subject, body, str(path), _now()))
                sent += 1

            open_count = len(open_issues)
            conn.execute(
                """INSERT INTO marketplace_entries
                   (contract_id, product_name, quality_score, status, open_issues,
                    last_run_id, updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(contract_id) DO UPDATE SET
                     product_name=excluded.product_name, quality_score=excluded.quality_score,
                     status=excluded.status, open_issues=excluded.open_issues,
                     last_run_id=excluded.last_run_id, updated_at=excluded.updated_at""",
                (cid, contract.get("name", cid), score if score is not None else 0,
                 status, open_count, run["run_id"] if run else None, _now()))
            products.append({
                "contract_id": cid, "product": contract.get("name", cid),
                "table": c["table_name"], "quality_score": score, "status": status,
                "open_issues": open_count, "owner": owner, "steward": steward,
                "last_run_id": run["run_id"] if run else None,
            })

        conn.commit()
        conn.close()
        marketplace = {
            "generated_at": _now(),
            "catalogue": "simulated data product marketplace (JSON, per brief)",
            "products": products,
        }
        (config.OUT_DIR / "marketplace.json").write_text(
            json.dumps(marketplace, indent=2), encoding="utf-8")
        marketplace["notifications_sent"] = sent
        return marketplace
