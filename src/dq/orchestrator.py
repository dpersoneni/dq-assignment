"""Deterministic orchestrator (ADR-04): fixed pipeline + scheduling + CLI dispatch.

No LLM is used here. Commands whose agents are built in later phases exit with
a clear "not implemented yet" message until then.
"""

import argparse
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import yaml

from . import config, db

BUSINESS_CONTEXT = {
    "customers": "Retail banking customers. email is the primary contact: never NULL, "
                 "unique, well-formed. kyc_status must be one of PENDING/VERIFIED/REJECTED. "
                 "Customers must be adults (18+). date_of_birth never in the future. phone present.",
    "accounts": "Bank accounts per customer. IBAN unique. currency must be a known ISO 4217 "
                "code and never NULL. CHECKING accounts cannot go negative (no overdraft). "
                "opened_date never in the future. Every account references an existing customer.",
    "transactions": "Card/transfer transactions; the freshest source. amount never NULL, never "
                    "exactly 0, sign must match transaction_type (DEBIT negative, CREDIT positive). "
                    "status in COMPLETED/PENDING/FAILED. Must reference an existing account; "
                    "currency must equal the account's currency; occurred_at never in the future.",
    "loans": "Loans per customer. principal > 0, interest_rate within 0-100, term_months > 0, "
             "at most one ACTIVE loan per customer.",
    "cards": "Payment cards. card_number (PAN) must be exactly 16 digits and unique. Must "
             "reference an existing account. An expired card must not remain ACTIVE.",
}


def dispatch(args) -> int:
    cmd = args.cmd
    if cmd == "seed":
        return _seed()
    if cmd == "init":
        return _init()
    if cmd == "sync":
        return _sync()
    if cmd == "compile":
        return _compile()
    if cmd == "draft":
        return _draft(args)
    if cmd == "approve":
        return _approve(args)
    if cmd == "run":
        return _run(args)
    if cmd == "tick":
        return _tick()
    if cmd == "dashboard":
        return _dashboard()
    if cmd == "notify":
        return _notify()
    if cmd == "demo":
        return _demo(args)
    if cmd == "report":
        from . import compare_to_manifest
        compare_to_manifest.build_report()
        return 0
    if cmd == "resolve":
        return _resolve(args)
    for later in ():
        if cmd == later:
            print(f"'{cmd}' is not implemented yet — it lands in a later phase of the plan.")
            return 0
    print(f"unknown command {cmd}")
    return 2


def _seed() -> int:
    config.ensure_dirs()
    r = subprocess.run([sys.executable, str(config.ROOT / "data" / "seed_dataset.py")])
    return r.returncode


def _init() -> int:
    conn = db.state()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    conn.close()
    print(f"state db ready at {config.STATE_DB}")
    print("tables:", ", ".join(tables))
    return 0


def _sync() -> int:
    from . import odcs
    conn = db.state()
    results = odcs.sync_contracts(conn)
    conn.close()
    ok = True
    for contract_id, status, errors in results:
        if errors:
            ok = False
            print(f"{contract_id}: INVALID")
            for e in errors:
                print(f"   - {e}")
        else:
            print(f"{contract_id}: {status}")
    return 0 if ok else 1


def _run(args) -> int:
    """Capability B, manual trigger: run one contract's checks now."""
    from .agents.a2_executor import QualityExecutor
    return QualityExecutor().run_contract(args.contract, triggered_by="manual")


def _tick() -> int:
    """Scheduler tick: full pipeline (A2 -> A3 -> A4) for every active contract whose
    next_run_due has passed."""
    conn = db.state()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    due = conn.execute(
        """SELECT contract_id FROM contracts
           WHERE status='active' AND next_run_due IS NOT NULL AND next_run_due <= ?
           ORDER BY next_run_due""", (now,)).fetchall()
    conn.close()
    ids = [r["contract_id"] for r in due]
    print(f"[tick] {len(ids)} contract(s) due at {now}: {ids}")
    return _pipeline(ids, "schedule")


def _pipeline(contract_ids: list, trigger: str) -> int:
    """Fixed pipeline per contract: A2 runs checks, then A3 renders the dashboard and
    A4 publishes notifications + marketplace once for all contracts (deterministic
    orchestrator, ADR-04)."""
    from .agents.a2_executor import QualityExecutor
    from .agents.a3_metrics import MetricsAgent
    from .agents.a4_notify import NotifyMarketplaceAgent

    rc = 0
    ex = QualityExecutor()
    for cid in contract_ids:
        rc |= ex.run_contract(cid, triggered_by=trigger)
    dash = MetricsAgent().render()
    print(f"[A3] dashboard written -> {dash}")
    result = NotifyMarketplaceAgent().publish()
    print(f"[A4] marketplace written -> {config.OUT_DIR / 'marketplace.json'} "
          f"({len(result['products'])} products, {result['notifications_sent']} new notifications)")
    return rc


def _dashboard() -> int:
    from .agents.a3_metrics import MetricsAgent
    print(f"[A3] dashboard -> {MetricsAgent().render()}")
    return 0


def _notify() -> int:
    from .agents.a4_notify import NotifyMarketplaceAgent
    r = NotifyMarketplaceAgent().publish()
    print(f"[A4] marketplace -> {config.OUT_DIR / 'marketplace.json'} "
          f"({len(r['products'])} products, {r['notifications_sent']} new notifications)")
    return 0


def _demo(args) -> int:
    """Full end-to-end flow for the recording: sync contracts, auto-approve drafts,
    run the pipeline for every active contract."""
    _sync()
    conn = db.state()
    drafts = [r["contract_id"] for r in conn.execute(
        "SELECT contract_id FROM contracts WHERE status='draft' ORDER BY contract_id")]
    active = [r["contract_id"] for r in conn.execute(
        "SELECT contract_id FROM contracts WHERE status='active' ORDER BY contract_id")]
    conn.close()
    for cid in drafts:
        ns = argparse.Namespace(contract=cid, auto_approve=True)
        _approve(ns)
        active.append(cid)
    print(f"[demo] pipeline for contracts: {sorted(active)}")
    return _pipeline(sorted(active), "manual")


def _resolve(args) -> int:
    """Capability E: Agent A5 suggests a root cause/fix for one issue (LLM, read-only),
    a human decides via the HITL gate, and only an accepted write-back is executed —
    by this orchestrator, in a transaction, on the source DB (ADR-07)."""
    import re as _re

    from . import tools as toolkit
    from .agents.a5_resolver import IssueResolver, _WRITE_SQL
    from .agents.a2_executor import QualityExecutor

    issue_id = args.issue
    auto = getattr(args, "auto_approve", False)
    conn = db.state()
    issue = conn.execute("SELECT * FROM issues WHERE issue_id = ?", (issue_id,)).fetchone()
    if issue is None:
        conn.close()
        print(f"no issue #{issue_id}")
        return 1
    if issue["status"] not in ("OPEN", "TRIAGED"):
        conn.close()
        print(f"issue #{issue_id} is {issue['status']} — nothing to resolve")
        return 0

    # 1) suggestion: reuse latest if TRIAGED, else ask A5 (LLM)
    if issue["status"] == "TRIAGED":
        sug = conn.execute(
            "SELECT * FROM issue_suggestions WHERE issue_id=? ORDER BY suggestion_id DESC LIMIT 1",
            (issue_id,)).fetchone()
        suggestion = {k: sug[k] for k in
                      ("root_cause", "suggested_fix", "confidence", "proposed_action", "proposed_sql")}
        suggestion["agent"] = sug["agent"]
        print(f"[A5] reusing existing suggestion #{sug['suggestion_id']} (agent={sug['agent']})")
    else:
        print(f"[A5] exploring issue #{issue_id} ({issue['contract_id']}.{issue['rule_id']}) with LLM ...")
        suggestion = IssueResolver().suggest(issue_id, {"auto_approve": auto})
        suggestion["agent"] = "a5-llm" if not suggestion.get("fallback") else "template-fallback"
        conn.execute(
            """INSERT INTO issue_suggestions
               (issue_id, agent, root_cause, suggested_fix, confidence, proposed_action,
                proposed_sql, created_at) VALUES (?,?,?,?,?,?,?,datetime('now'))""",
            (issue_id, suggestion["agent"], suggestion["root_cause"], suggestion["suggested_fix"],
             suggestion["confidence"], suggestion["proposed_action"], suggestion.get("proposed_sql")))
        conn.execute("UPDATE issues SET status='TRIAGED' WHERE issue_id=?", (issue_id,))
        conn.commit()

    print(f"[A5] root_cause   : {suggestion['root_cause']}")
    print(f"[A5] suggested_fix: {suggestion['suggested_fix']}")
    print(f"[A5] confidence   : {suggestion['confidence']}  action: {suggestion['proposed_action']}")
    if suggestion.get("proposed_sql"):
        print(f"[A5] proposed_sql : {suggestion['proposed_sql']}")

    # 2) HITL decision
    choice = toolkit.execute("ask_human", {
        "question": (f"Issue #{issue_id} ({issue['contract_id']}.{issue['rule_id']}, "
                     f"{issue['severity']}): accept the suggestion "
                     f"(action={suggestion['proposed_action']}), reject it, or close the issue?"),
        "options": ["accept", "reject", "close"],
    }, {"auto_approve": auto})

    # 3) record decision + apply outcome
    wrote = False
    if choice == "accept" and suggestion["proposed_action"] == "propose_write_back" \
            and suggestion.get("proposed_sql") and _WRITE_SQL.match(suggestion["proposed_sql"]):
        # the ONLY path that writes to the source DB: orchestrator, explicit approval, one transaction
        import sqlite3
        src = sqlite3.connect(config.SOURCE_DB)
        try:
            with src:  # transaction: commits on success, rolls back on error
                src.execute(suggestion["proposed_sql"])
            wrote = True
            print(f"[HITL] approved write-back executed in one transaction: {suggestion['proposed_sql']}")
        except Exception as e:
            print(f"[HITL] write-back FAILED and was rolled back: {e}")
        finally:
            src.close()
    new_status = {"accept": "RESOLVED", "reject": "OPEN", "close": "CLOSED"}[choice]
    conn.execute(
        "INSERT INTO issue_decisions (issue_id, suggestion_id, decision, decided_by, decided_at, note)"
        " VALUES (?,?,?,?,datetime('now'),?)",
        (issue_id, conn.execute("SELECT MAX(suggestion_id) AS s FROM issue_suggestions WHERE issue_id=?",
                                (issue_id,)).fetchone()["s"],
         choice, "auto-approve" if auto else "human",
         f"action={suggestion['proposed_action']}; write_back={'yes' if wrote else 'no'}"))
    conn.execute("UPDATE issues SET status=? WHERE issue_id=?", (new_status, issue_id))
    conn.commit()
    conn.close()
    print(f"[A5] issue #{issue_id} -> {new_status} (decision={choice}, write_back={wrote})")

    # 4) if we wrote to the source, re-run the contract's checks to verify the fix
    if wrote:
        rc = QualityExecutor().run_contract(issue["contract_id"], triggered_by="manual")
        conn = db.state()
        still = conn.execute(
            "SELECT COUNT(*) FROM check_results c JOIN runs r ON c.run_id=r.run_id"
            " WHERE r.contract_id=? AND c.rule_id=? AND c.status='FAIL'"
            " AND r.run_id=(SELECT MAX(run_id) FROM runs WHERE contract_id=?)",
            (issue["contract_id"], issue["rule_id"], issue["contract_id"])).fetchone()[0]
        conn.close()
        print(f"[verify] rule {issue['rule_id']} still failing in latest run: {'YES' if still else 'NO — fix confirmed'}")
        return rc
    return 0


def _compile() -> int:
    """Phase 4 self-test: compile all contract rules to SQL, run them against the
    source, and compare per-table defective-row totals with the defect manifest."""
    from . import rules
    return rules.selftest()


def _draft(args) -> int:
    """Agent A1 drafts the contract (LLM); validation is deterministic; the file is
    registered as draft. Activation happens only via the approve HITL gate."""
    from . import odcs
    from .agents import a1_contract

    table = args.table
    ctx = {"auto_approve": getattr(args, "auto_approve", False)}
    print(f"[A1] drafting ODCS contract for table '{table}' (model={config.MODEL}) ...")
    yaml_text = a1_contract.draft(table, BUSINESS_CONTEXT.get(table, "generic table"), ctx)

    contract = odcs.normalize_contract(yaml.safe_load(yaml_text))
    conn = db.source_ro()
    tables = odcs._source_tables(conn)
    conn.close()
    errors = odcs.validate_contract(contract, tables)
    if errors:
        print("A1's draft is INVALID — not saved:")
        for e in errors:
            print("  -", e)
        return 1

    path = config.CONTRACTS_DIR / f"{table}.yaml"
    path.write_text(yaml.safe_dump(contract, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")
    print(f"[A1] draft written to {path}")
    conn = db.state()
    odcs.sync_contracts(conn)
    conn.close()
    print(f"registered as draft. Activate with: python run.py approve --contract {table}")
    return 0


def _approve(args) -> int:
    """HITL gate (capability A): a human approves a draft contract before it becomes active."""
    from . import tools as toolkit

    auto = getattr(args, "auto_approve", False)
    conn = db.state()
    row = conn.execute("SELECT * FROM contracts WHERE contract_id = ?",
                       (args.contract,)).fetchone()
    if row is None:
        conn.close()
        print(f"unknown contract '{args.contract}' — run `python run.py sync` first")
        return 1
    if row["status"] == "active":
        conn.close()
        print(f"{args.contract} is already active")
        return 0

    choice = toolkit.execute("ask_human", {
        "question": f"Approve contract '{args.contract}' (hash {row['yaml_hash']})? "
                    "It becomes active and its checks will run on schedule.",
        "options": ["approve", "reject"],
    }, {"auto_approve": auto})

    if choice == "approve":
        next_due = None
        if row["schedule_min"]:
            next_due = (datetime.now(timezone.utc)
                        + timedelta(minutes=row["schedule_min"])).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE contracts SET status='active', approved_by=?, approved_at=datetime('now'),"
            " next_run_due=? WHERE contract_id=?",
            ("auto-approve" if auto else "human", next_due, args.contract))
        conn.commit()
        print(f"contract '{args.contract}' is ACTIVE (approved_by="
              f"{'auto-approve' if auto else 'human'}, next_run_due={next_due})")
    else:
        conn.execute(
            "UPDATE contracts SET status='rejected', approved_at=datetime('now') WHERE contract_id=?",
            (args.contract,))
        conn.commit()
        print(f"contract '{args.contract}' rejected")
    conn.close()
    return 0
