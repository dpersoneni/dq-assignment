-- Blackboard schema (ADR-11): all inter-agent state lives in dq.db.

CREATE TABLE IF NOT EXISTS contracts (
    contract_id   TEXT PRIMARY KEY,             -- e.g. 'customers'
    table_name    TEXT NOT NULL,
    file_path     TEXT NOT NULL,                -- contracts/<table>.yaml
    yaml_hash     TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'draft',-- draft | active | rejected
    approved_by   TEXT,
    approved_at   TEXT,
    schedule_min  INTEGER,                      -- SLA: run every N minutes (NULL = manual only)
    next_run_due  TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id    TEXT NOT NULL REFERENCES contracts(contract_id),
    triggered_by   TEXT NOT NULL,               -- schedule | manual
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    status         TEXT NOT NULL DEFAULT 'RUNNING',  -- RUNNING|COMPLETED|COMPLETED_WITH_ERRORS|FAILED|SKIPPED
    rules_executed INTEGER NOT NULL DEFAULT 0,
    rules_errored  INTEGER NOT NULL DEFAULT 0,
    issues_found   INTEGER NOT NULL DEFAULT 0,
    error          TEXT
);

CREATE TABLE IF NOT EXISTS check_results (
    result_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES runs(run_id),
    rule_id       TEXT NOT NULL,
    table_name    TEXT NOT NULL,
    column_name   TEXT,
    dimension     TEXT NOT NULL,
    severity      TEXT NOT NULL,
    status        TEXT NOT NULL,               -- PASS | FAIL | ERROR
    rows_affected INTEGER NOT NULL DEFAULT 0,
    sample        TEXT,                        -- JSON array of up to 5 offending rows
    executed_sql  TEXT,
    error         TEXT,
    checked_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS issues (
    issue_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id   TEXT NOT NULL,
    rule_id       TEXT NOT NULL,
    fingerprint   TEXT NOT NULL,               -- hash(contract_id + rule_id) for dedup
    status        TEXT NOT NULL DEFAULT 'OPEN', -- OPEN|TRIAGED|RESOLVED|REJECTED|CLOSED
    severity      TEXT NOT NULL,
    dimension     TEXT NOT NULL,
    table_name    TEXT NOT NULL,
    column_name   TEXT,
    rows_affected INTEGER NOT NULL DEFAULT 0,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    occurrences   INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_open_issue ON issues(fingerprint) WHERE status = 'OPEN';

CREATE TABLE IF NOT EXISTS issue_suggestions (
    suggestion_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id       INTEGER NOT NULL REFERENCES issues(issue_id),
    agent          TEXT NOT NULL,               -- 'a5-llm' | 'template-fallback'
    root_cause     TEXT,
    suggested_fix  TEXT,
    confidence     TEXT,
    proposed_action TEXT NOT NULL,              -- no_data_change | flag_for_human | propose_write_back
    proposed_sql   TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS issue_decisions (
    decision_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id     INTEGER NOT NULL REFERENCES issues(issue_id),
    suggestion_id INTEGER REFERENCES issue_suggestions(suggestion_id),
    decision     TEXT NOT NULL,                 -- accept | reject | close
    decided_by   TEXT NOT NULL,
    decided_at   TEXT NOT NULL,
    note         TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id        INTEGER REFERENCES issues(issue_id),
    run_id          INTEGER REFERENCES runs(run_id),
    channel         TEXT NOT NULL DEFAULT 'file',
    to_owner        TEXT NOT NULL,
    to_steward      TEXT NOT NULL,
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    payload_path    TEXT,
    sent_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS marketplace_entries (
    contract_id   TEXT PRIMARY KEY REFERENCES contracts(contract_id),
    product_name  TEXT NOT NULL,
    quality_score REAL NOT NULL,                -- 0-100
    status        TEXT NOT NULL,                -- PASSING | DEGRADED | FAILING
    open_issues   INTEGER NOT NULL DEFAULT 0,
    last_run_id   INTEGER REFERENCES runs(run_id),
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS steps (
    step_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER REFERENCES runs(run_id),
    contract_id TEXT,
    agent       TEXT NOT NULL,
    action      TEXT NOT NULL,
    status      TEXT NOT NULL,                  -- ok | skipped | error
    detail      TEXT,
    at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_calls (
    call_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    agent             TEXT NOT NULL,
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    iterations        INTEGER,
    at                TEXT NOT NULL
);
