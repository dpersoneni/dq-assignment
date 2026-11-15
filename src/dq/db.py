"""Database access. Two physically separate connections:

- source_ro(): the source data (data/banking.db), ALWAYS read-only — this is how
  the "agents never write to source data" guardrail is enforced in code (ADR-06/07).
- state(): the product's own blackboard (dq.db), read-write.
"""

import sqlite3

from . import config


def source_ro() -> sqlite3.Connection:
    """Read-only connection to the source database. Raises if the dataset was not seeded."""
    if not config.SOURCE_DB.exists():
        raise FileNotFoundError(
            f"{config.SOURCE_DB} not found — run `python run.py seed` first")
    uri = f"file:{config.SOURCE_DB.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def state() -> sqlite3.Connection:
    """Read-write connection to the state DB; applies schema.sql (idempotent)."""
    config.ensure_dirs()
    conn = sqlite3.connect(config.STATE_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(config.SCHEMA_SQL.read_text(encoding="utf-8"))
    return conn
