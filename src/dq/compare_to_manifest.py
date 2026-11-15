"""Detection report (Task 1): compare detected issues with the seeded defect manifest.

Deterministic. For each manifest defect we know the contract rule that should catch it
(RULE_TO_DEFECT, 1:1 by construction — see contracts/*.yaml and data/seed_dataset.py).
A defect is "detected" if its rule FAILED in the contract's latest completed run.
Failing rules that map to no manifest defect are reported as false positives.

Outputs:
  out/detection_report.csv          — one row per seeded defect (the Excel tab deliverable:
                                      seeded defect, rule, detected Y/N, severity, ...)
  out/detection_report_summary.txt  — found / missed / false positives + detection rate
"""

import csv
from pathlib import Path

from . import config, db

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data" / "defect_manifest.csv"

# rule_id (in contracts/*.yaml) -> defect_id (in data/defect_manifest.csv)
RULE_TO_DEFECT = {
    "email_not_null": "D01", "email_unique": "D02", "email_format": "D03",
    "dob_not_future": "D04", "dob_min_age_18": "D05", "kyc_status_enum": "D06",
    "phone_not_null": "D07",
    "account_customer_fk": "D08", "currency_not_null": "D09", "currency_iso4217": "D10",
    "balance_checking_non_negative": "D11", "opened_not_future": "D12", "iban_unique": "D13",
    "amount_not_null": "D14", "amount_nonzero": "D15", "tx_account_fk": "D16",
    "occurred_not_future": "D17", "status_enum": "D18",
    "tx_currency_matches_account": "D19", "amount_sign_matches_type": "D20",
    "interest_rate_range": "D21", "term_months_positive": "D22", "principal_positive": "D23",
    "max_one_active_loan_per_customer": "D24",
    "card_not_expired_while_active": "D25", "card_number_format": "D26",
    "card_number_unique": "D27", "card_account_fk": "D28",
}


def build_report() -> str:
    conn = db.state()

    # latest completed run per contract
    latest = {r["contract_id"]: r["run_id"] for r in conn.execute(
        """SELECT contract_id, MAX(run_id) AS run_id FROM runs
           WHERE status IN ('COMPLETED','COMPLETED_WITH_ERRORS')
           GROUP BY contract_id""")}

    # failing rules in those latest runs: (contract_id, rule_id) -> rows_affected
    failing = {}
    for r in conn.execute(
            """SELECT r.contract_id, c.rule_id, c.rows_affected
               FROM check_results c JOIN runs r ON c.run_id = r.run_id
               WHERE c.status = 'FAIL' AND c.run_id IN (SELECT MAX(run_id) FROM runs
                   WHERE status IN ('COMPLETED','COMPLETED_WITH_ERRORS')
                   GROUP BY contract_id)"""):
        failing[(r["contract_id"], r["rule_id"])] = r["rows_affected"]

    # contract_id per table (manifest is keyed by table_name)
    table_to_contract = {r["table_name"]: r["contract_id"] for r in
                         conn.execute("SELECT contract_id, table_name FROM contracts")}
    conn.close()

    with open(MANIFEST, newline="", encoding="utf-8") as f:
        manifest = list(csv.DictReader(f))

    defect_to_rule = {v: k for k, v in RULE_TO_DEFECT.items()}
    if len(defect_to_rule) != len(RULE_TO_DEFECT):
        raise ValueError("RULE_TO_DEFECT must be 1:1")

    rows = []
    for m in manifest:
        rule_id = defect_to_rule.get(m["defect_id"])
        cid = table_to_contract.get(m["table_name"])
        detected = rule_id is not None and (cid, rule_id) in failing
        rows.append({
            "defect_id": m["defect_id"],
            "table_name": m["table_name"],
            "column_name": m["column_name"],
            "defect_type": m["defect_type"],
            "quality_dimension": m["quality_dimension"],
            "severity": m["severity"],
            "manifest_rows_affected": m["rows_affected"],
            "rule_id": rule_id or "",
            "detected": "Y" if detected else "N",
            "detected_rows": failing.get((cid, rule_id), 0) if detected else 0,
        })

    false_positives = [
        {"contract_id": cid, "rule_id": rid, "rows_affected": n}
        for (cid, rid), n in failing.items() if rid not in RULE_TO_DEFECT
    ]

    found = sum(1 for r in rows if r["detected"] == "Y")
    missed = [r for r in rows if r["detected"] == "N"]
    total = len(rows)
    rate = found / total if total else 0.0

    out_csv = config.OUT_DIR / "detection_report.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    summary = [
        "Detection report — latest completed run per contract vs data/defect_manifest.csv",
        f"seeded defects in manifest : {total}",
        f"found (detected = Y)       : {found}",
        f"missed (detected = N)      : {len(missed)}"
        + (": " + ", ".join(r["defect_id"] for r in missed) if missed else ""),
        f"false positives            : {len(false_positives)}"
        + (": " + ", ".join(f"{p['contract_id']}.{p['rule_id']}" for p in false_positives)
           if false_positives else ""),
        f"detection rate             : {rate:.1%} ({found}/{total})",
        "",
        "Note: all checks are contract-driven (rules compiled from contracts/*.yaml, executed",
        "read-only). Time-based rules evaluated at the fixed as-of date 2025-06-30 (ADR-03).",
    ]
    out_txt = config.OUT_DIR / "detection_report_summary.txt"
    out_txt.write_text("\n".join(summary) + "\n", encoding="utf-8")

    print("\n".join(summary))
    print(f"\nCSV written -> {out_csv}")
    return str(out_csv)


if __name__ == "__main__":
    build_report()
