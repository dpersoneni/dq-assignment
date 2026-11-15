"""ODCS v3.x contract loading, validation and registration (capability A).

Contracts are YAML files in contracts/, validated against the official ODCS
v3.0.0 JSON Schema (vendored at src/dq/odcs-json-schema-v3.0.0.json, from
bitol-io/open-data-contract-standard) plus semantic checks against the actual
source database schema.
"""

import hashlib
import json
from pathlib import Path

import jsonschema
import yaml

from . import config, db

SCHEMA_PATH = Path(__file__).with_name("odcs-json-schema-v3.0.0.json")
_odcs_schema = None


def _schema() -> dict:
    global _odcs_schema
    if _odcs_schema is None:
        _odcs_schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return _odcs_schema


def load_yaml(path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def contract_hash(contract: dict) -> str:
    return hashlib.sha1(json.dumps(contract, sort_keys=True).encode()).hexdigest()[:16]


_LIST_FIELDS = ("tags", "servers", "team", "support", "slaProperties", "customProperties")


def normalize_contract(contract: dict) -> dict:
    """Best-effort, deterministic repair of common LLM format slips before validation:
    a single object/scalar where ODCS wants a list, and `properties` given as a
    mapping instead of a list. The ODCS schema is still the final gate."""
    import copy

    c = copy.deepcopy(contract)
    for k in _LIST_FIELDS:
        v = c.get(k)
        if isinstance(v, (dict, str)):
            c[k] = [v]
    for obj in c.get("schema", []) or []:
        props = obj.get("properties")
        if isinstance(props, dict):
            obj["properties"] = [{"name": k, **v} for k, v in props.items()]
        for p in obj.get("properties", []) or []:
            if isinstance(p.get("quality"), dict):
                p["quality"] = [p["quality"]]
        if isinstance(obj.get("quality"), dict):
            obj["quality"] = [obj["quality"]]
    return c


def _source_tables(conn) -> dict:
    tables = {}
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        tables[t] = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
    return tables


def validate_contract(contract: dict, source_tables: dict | None = None) -> list:
    """Return a list of error strings; an empty list means the contract is valid."""
    contract = normalize_contract(contract)
    try:
        jsonschema.validate(contract, _schema())
    except jsonschema.ValidationError as e:
        path = "/".join(str(p) for p in e.absolute_path) or "<root>"
        return [f"ODCS schema violation at {path}: {e.message}"]

    errors = []
    schema_objs = contract.get("schema", [])
    if len(schema_objs) != 1:
        return ["contract must describe exactly one schema object (one table)"]
    obj = schema_objs[0]
    table = obj["name"]

    if source_tables is not None and table not in source_tables:
        errors.append(f"schema object '{table}' does not exist in the source database")
        return errors
    if source_tables is not None:
        cols = set(source_tables[table])
        props = {p["name"] for p in obj.get("properties", [])}
        missing = props - cols
        if missing:
            errors.append(f"properties not present in source table {table}: {sorted(missing)}")

    rule_names = []
    for q in obj.get("quality", []) or []:
        rule_names.append(q.get("name"))
    for p in obj.get("properties", []):
        for q in p.get("quality", []) or []:
            rule_names.append(q.get("name"))
    for n in rule_names:
        if not n:
            errors.append("every quality rule must have a name")
    if len(rule_names) != len(set(rule_names)):
        errors.append("duplicate rule names in contract")
    return errors


def validate_file(path) -> tuple:
    """Load, normalize and validate one contract file. Returns (normalized contract, errors)."""
    contract = normalize_contract(load_yaml(path))
    conn = db.source_ro()
    tables = _source_tables(conn)
    conn.close()
    return contract, validate_contract(contract, tables)


def get_custom(contract: dict, key: str, default=None):
    for c in contract.get("customProperties", []) or []:
        if c.get("property") == key:
            return c.get("value", default)
    return default


def sync_contracts(conn) -> list:
    """Validate all contracts/*.yaml and register/refresh them in the contracts table.

    A contract whose YAML changed after approval is moved back to 'draft'
    (re-approval required — that is the HITL gate of capability A).
    Returns a list of (contract_id, status, errors).
    """
    out = []
    for path in sorted(config.CONTRACTS_DIR.glob("*.yaml")):
        contract, errors = validate_file(path)
        if errors:
            out.append((path.stem, "invalid", errors))
            continue
        table = contract["schema"][0]["name"]
        h = contract_hash(contract)
        schedule_min = int(get_custom(contract, "dqScheduleMin", 0) or 0) or None
        row = conn.execute(
            "SELECT yaml_hash, status FROM contracts WHERE contract_id = ?", (table,)).fetchone()
        if row is None:
            status = "draft"
        elif row["status"] == "active" and row["yaml_hash"] != h:
            status = "draft"  # changed after approval → needs re-approval
        else:
            status = row["status"]
        conn.execute(
            """INSERT INTO contracts (contract_id, table_name, file_path, yaml_hash, status,
                                      schedule_min, created_at)
               VALUES (?,?,?,?,?,?,datetime('now'))
               ON CONFLICT(contract_id) DO UPDATE SET
                   table_name=excluded.table_name, file_path=excluded.file_path,
                   yaml_hash=excluded.yaml_hash, status=excluded.status,
                   schedule_min=excluded.schedule_min""",
            (table, table, str(path), h, status, schedule_min))
        out.append((table, status, []))
    conn.commit()
    return out
