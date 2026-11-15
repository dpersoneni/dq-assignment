"""Deterministic rule-to-SQL compiler (ADR-06).

Contracts drive the checks: every quality rule in an ODCS contract is compiled
here into one read-only SQL statement that returns the offending primary keys.
Unknown rule types fail loudly — never silently skipped, never invented by an LLM.
This module contains no LLM calls.
"""

from pathlib import Path

from . import config, db

ROOT = Path(__file__).resolve().parents[2]


def _compile_rule(table: str, field: str | None, rule: dict, pk: str, as_of: str) -> dict:
    name = rule.get("name")
    rtype = rule.get("type")
    base = {"rule_id": name, "table": table, "column": field,
            "dimension": rule.get("dimension", "conformity"),
            "severity": rule.get("severity", "MEDIUM")}

    if rtype == "sql":
        q = (rule["query"]
             .replace("${table}", f'"{table}"')
             .replace("${asOfMonth}", as_of[:7])
             .replace("${asOf}", as_of))
        return {**base, "sql": q}

    if rtype == "library":
        if field is None:
            raise ValueError(f"rule {name!r}: library rules must sit on a property (field-level)")
        r = rule.get("rule")
        if r == "nullCount" and rule.get("mustBe") == 0:
            sql = f'SELECT "{pk}" FROM "{table}" WHERE "{field}" IS NULL'
        elif r == "duplicateCount" and rule.get("mustBe") == 0:
            sql = (f'SELECT "{pk}" FROM "{table}" WHERE "{field}" IN '
                   f'(SELECT "{field}" FROM "{table}" WHERE "{field}" IS NOT NULL '
                   f'GROUP BY "{field}" HAVING COUNT(*) > 1)')
        else:
            raise ValueError(
                f"rule {name!r}: unsupported library rule {r!r} (mustBe={rule.get('mustBe')!r})")
        return {**base, "sql": sql}

    raise ValueError(f"rule {name!r}: unsupported rule type {rtype!r}")


def compile_contract(contract: dict, as_of: str = config.AS_OF) -> list:
    """Compile all quality rules of one ODCS contract into executable SQL dicts."""
    from . import odcs  # local import to keep module import light

    obj = contract["schema"][0]
    table = obj["name"]
    conn = db.source_ro()
    pk = "rowid"
    for r in conn.execute(f'PRAGMA table_info("{table}")'):
        if r[5]:  # pk flag
            pk = r[1]
            break
    conn.close()

    rules = []
    for q in obj.get("quality", []) or []:
        rules.append(_compile_rule(table, None, q, pk, as_of))
    for p in obj.get("properties", []):
        for q in p.get("quality", []) or []:
            rules.append(_compile_rule(table, p["name"], q, pk, as_of))
    return rules


def compile_all_contracts() -> dict:
    """Validate + compile every contracts/*.yaml. Returns {table: [rules]}."""
    from . import odcs

    out = {}
    for path in sorted((ROOT / "contracts").glob("*.yaml")):
        contract, errors = odcs.validate_file(path)
        if errors:
            raise ValueError(f"{path.name}: {errors}")
        table = contract["schema"][0]["name"]
        out[table] = compile_contract(contract)
    return out


def selftest() -> int:
    """Compile every contract, execute each rule against the source DB, and compare
    the per-table defective-row totals with data/defect_manifest.csv."""
    import csv

    from . import odcs

    manifest = {}
    with open(ROOT / "data" / "defect_manifest.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            t = row["table_name"]
            manifest[t] = manifest.get(t, 0) + int(row["rows_affected"])

    conn = db.source_ro()
    results = {}
    for path in sorted((ROOT / "contracts").glob("*.yaml")):
        contract, errors = odcs.validate_file(path)
        assert not errors, errors
        for r in compile_contract(contract):
            n = conn.execute(f'SELECT COUNT(*) FROM ({r["sql"]})').fetchone()[0]
            results[r["table"]] = results.get(r["table"], 0) + n
            print(f'{r["table"]:14s} {r["rule_id"]:34s} rows={n}')
    conn.close()

    print()
    ok = True
    for t in sorted(manifest):
        got = results.get(t, 0)
        match = "OK" if got == manifest[t] else "MISMATCH"
        if match != "OK":
            ok = False
        print(f"{t:14s} manifest={manifest[t]:3d}  compiled-rules={got:3d}  {match}")
    print("TOTAL", sum(manifest.values()), "vs", sum(results.values()))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
