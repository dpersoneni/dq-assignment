#!/usr/bin/env python3
"""Seed a fictitious banking dataset (SQLite) with deliberately seeded data-quality defects.

Outputs (written next to this script):
  banking.db            SQLite database with 5 related tables
  defect_manifest.csv   one row per seeded defect: table, column, defect type, rows affected

Run from the repo root:  python data/seed_dataset.py
"""

import csv
import os
import random
import sqlite3
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "banking.db")
MANIFEST_PATH = os.path.join(BASE_DIR, "defect_manifest.csv")

# Fixed as-of date for all time-based defect checks, so the manifest is
# reproducible no matter when the seed script is re-run.
AS_OF = "2025-06-30"

SCHEMA = """
CREATE TABLE customers (
    customer_id   INTEGER PRIMARY KEY,
    full_name     TEXT NOT NULL,
    email         TEXT,
    phone         TEXT,
    date_of_birth TEXT,
    country       TEXT,
    kyc_status    TEXT
);

CREATE TABLE accounts (
    account_id   INTEGER PRIMARY KEY,
    iban         TEXT,
    customer_id  INTEGER REFERENCES customers(customer_id),
    account_type TEXT,
    currency     TEXT,
    balance      REAL,
    opened_date  TEXT,
    status       TEXT
);

CREATE TABLE transactions (
    transaction_id   INTEGER PRIMARY KEY,
    account_id       INTEGER REFERENCES accounts(account_id),
    amount           REAL,
    currency         TEXT,
    transaction_type TEXT,
    occurred_at      TEXT,
    merchant         TEXT,
    status           TEXT
);

CREATE TABLE loans (
    loan_id       INTEGER PRIMARY KEY,
    customer_id   INTEGER REFERENCES customers(customer_id),
    principal     REAL,
    interest_rate REAL,
    start_date    TEXT,
    term_months   INTEGER,
    status        TEXT
);

CREATE TABLE cards (
    card_id      INTEGER PRIMARY KEY,
    card_number  TEXT,
    account_id   INTEGER REFERENCES accounts(account_id),
    customer_id  INTEGER REFERENCES customers(customer_id),
    expiry_date  TEXT,
    credit_limit REAL,
    status       TEXT
);
"""

# ---------------------------------------------------------------- clean data

CUSTOMERS = [
    # customer_id, full_name, email, phone, date_of_birth, country, kyc_status
    (1,  "Alice Anderson", "alice.anderson@example.com", "+1-555-0101", "1985-04-12", "US", "VERIFIED"),
    (2,  "Bob Brown",      "bob.brown@example.com",      "+1-555-0102", "1978-09-30", "US", "VERIFIED"),
    (3,  "Carla Cruz",     "carla.cruz@example.com",     "+34-555-0103", "1990-01-25", "ES", "VERIFIED"),
    (4,  "David Diaz",     "david.diaz@example.com",     None,          "1982-07-08", "US", "VERIFIED"),  # D07 phone NULL
    (5,  "Emma Evans",     "emma.evans@example.com",     "+44-555-0105", "1993-11-17", "GB", "UNKNOWN"),   # D06 invalid kyc_status
    (6,  "Finn Fox",       "finn.fox@example.com",       "+1-555-0106", "2015-03-03", "US", "PENDING"),   # D05 under 18
    (7,  "Grace Green",    "grace.green@example.com",    "+1-555-0107", "2999-01-01", "US", "VERIFIED"),  # D04 future DOB
    (8,  "Hugo Harris",    "missing@domain",             "+1-555-0108", "1988-12-01", "US", "VERIFIED"),  # D03 malformed email
    (9,  "Iris Irwin",     "not-an-email",               "+1-555-0109", "1995-06-19", "CA", "PENDING"),   # D03 malformed email
    (10, "Jack Jones",     "alice.anderson@example.com", "+1-555-0110", "1980-02-14", "US", "VERIFIED"),  # D02 duplicate email
    (11, "Karen King",     None,                         "+1-555-0111", "1987-08-22", "US", "VERIFIED"),  # D01 email NULL
    (12, "Liam Lee",       None,                         "+1-555-0112", "1991-10-05", "AU", "PENDING"),   # D01 email NULL
]

ACCOUNTS = [
    # account_id, iban, customer_id, account_type, currency, balance, opened_date, status
    (1,  "US00DQBK0000000001", 1,   "CHECKING", "USD", 15420.50, "2019-03-15", "ACTIVE"),
    (2,  "US00DQBK0000000002", 1,   "SAVINGS",  "USD", 8300.00,  "2019-03-15", "ACTIVE"),
    (3,  "ES00DQBK0000000003", 3,   "CHECKING", "EUR", 2310.75,  "2020-06-01", "ACTIVE"),
    (4,  "US00DQBK0000000004", 2,   "CHECKING", "USD", 950.20,   "2018-11-20", "ACTIVE"),
    (5,  "GB00DQBK0000000005", 5,   "SAVINGS",  "GBP", 12000.00, "2021-01-10", "ACTIVE"),
    (6,  "US00DQBK0000000006", 6,   "CHECKING", "USD", 120.00,   "2022-05-05", "ACTIVE"),
    (7,  "CA00DQBK0000000007", 9,   "CHECKING", "CAD", 3400.00,  "2020-09-09", "ACTIVE"),
    (8,  "US00DQBK0000000008", 8,   "SAVINGS",  "USD", 5100.00,  "2017-02-28", "ACTIVE"),
    (9,  "AU00DQBK0000000009", 12,  "CHECKING", "AUD", 2780.40,  "2023-04-18", "ACTIVE"),
    (10, "US00DQBK0000000010", 10,  "CREDIT",   "USD", 0.00,     "2021-12-01", "ACTIVE"),
    (11, "US00DQBK0000000011", 4,   "SAVINGS",  "USD", 640.00,   "2999-06-01", "ACTIVE"),   # D12 future opened_date
    (12, "US00DQBK0000000012", 7,   "CHECKING", "USD", -500.00,  "2019-08-08", "ACTIVE"),   # D11 negative balance on CHECKING
    (13, "US00DQBK0000000013", 3,   "SAVINGS",  "XXX", 990.00,   "2022-02-02", "ACTIVE"),   # D10 invalid currency
    (14, "US00DQBK0000000014", 2,   "CHECKING", None,  210.00,   "2023-07-07", "ACTIVE"),   # D09 currency NULL
    (15, "US00DQBK0000000001", 11,  "SAVINGS",  "USD", 1500.00,  "2024-01-12", "ACTIVE"),   # D13 duplicate IBAN
    (16, "US00DQBK0000000016", 999, "CHECKING", "USD", 100.00,   "2023-03-03", "ACTIVE"),   # D08 orphan customer_id
]

LOANS = [
    # loan_id, customer_id, principal, interest_rate, start_date, term_months, status
    (1, 1,  10000.00, 5.5,  "2023-01-15", 36, "ACTIVE"),
    (2, 3,  25000.00, 4.2,  "2022-06-01", 60, "PAID_OFF"),
    (3, 5,  8000.00,  6.9,  "2024-02-10", 24, "ACTIVE"),
    (4, 8,  15000.00, 5.0,  "2021-11-20", 48, "PAID_OFF"),
    (5, 2,  5000.00,  -2.5, "2023-05-05", 12, "ACTIVE"),   # D21 negative interest_rate
    (6, 10, 12000.00, 7.5,  "2024-03-01", 0,  "ACTIVE"),   # D22 zero term_months
    (7, 4,  -1000.00, 3.9,  "2023-09-09", 12, "ACTIVE"),   # D23 negative principal
    (8, 1,  7000.00,  6.0,  "2024-06-01", 24, "ACTIVE"),   # D24 customer 1 has 2 ACTIVE loans
]

CARDS = [
    # card_id, card_number, account_id, customer_id, expiry_date, credit_limit, status
    (1,  "4111111111111111", 1,   1,  "2026-12", 5000.00, "ACTIVE"),
    (2,  "4222222222222222", 1,   1,  "2027-01", 2000.00, "ACTIVE"),
    (3,  "5333333333333333", 3,   3,  "2025-11", 3000.00, "ACTIVE"),
    (4,  "4444444444444444", 4,   2,  "2026-03", 1500.00, "ACTIVE"),
    (5,  "5555555555555555", 5,   5,  "2027-06", 8000.00, "ACTIVE"),
    (6,  "6666666666666666", 7,   9,  "2025-09", 1200.00, "BLOCKED"),
    (7,  "7777777777777777", 8,   8,  "2026-08", 4000.00, "ACTIVE"),
    (8,  "8888888888888888", 9,   12, "2027-02", 2500.00, "ACTIVE"),
    (9,  "4111111111111111", 2,   1,  "2026-10", 1000.00, "ACTIVE"),   # D27 duplicate card_number
    (10, "123",              10,  10, "2025-12", 500.00,  "ACTIVE"),   # D26 card_number not 16 digits
    (11, "9999999999999999", 11,  4,  "2020-01", 3000.00, "ACTIVE"),   # D25 expired but still ACTIVE
    (12, "1212121212121212", 999, 11, "2027-05", 1000.00, "ACTIVE"),   # D28 orphan account_id
]

ACCOUNT_CURRENCY = {1: "USD", 2: "USD", 3: "EUR", 4: "USD", 5: "GBP",
                    6: "USD", 7: "CAD", 8: "USD", 9: "AUD", 10: "USD"}

MERCHANTS = ["Amazon", "Uber", "Whole Foods", "Shell", "Netflix",
             "Starbucks", "Apple Store", "IKEA", "Zara", "Local Cafe"]


def clean_transactions():
    """48 deterministic, clean transactions across accounts 1-10 (all in the past)."""
    rng = random.Random(42)
    start = datetime(2023, 1, 1)
    rows = []
    for i in range(1, 49):
        acct = rng.choice(list(ACCOUNT_CURRENCY))
        ttype = rng.choice(["DEBIT", "CREDIT"])
        amount = -round(rng.uniform(1, 500), 2) if ttype == "DEBIT" else round(rng.uniform(1, 1000), 2)
        ts = start + timedelta(days=rng.randint(0, 730),
                               hours=rng.randint(0, 23),
                               minutes=rng.randint(0, 59))
        status = "COMPLETED" if rng.random() > 0.15 else "PENDING"
        rows.append((i, acct, amount, ACCOUNT_CURRENCY[acct], ttype,
                     ts.strftime("%Y-%m-%d %H:%M:%S"), rng.choice(MERCHANTS), status))
    return rows


def defective_transactions():
    """Hand-crafted defective transactions (IDs 49-57)."""
    return [
        (49, 3,   None,   "EUR", "DEBIT",  "2024-05-01 10:00:00", "Local Cafe",  "COMPLETED"),  # D14 amount NULL
        (50, 4,   None,   "USD", "DEBIT",  "2024-05-02 11:00:00", "Shell",       "COMPLETED"),  # D14 amount NULL
        (51, 1,   0.00,   "USD", "DEBIT",  "2024-05-03 12:00:00", "Uber",        "COMPLETED"),  # D15 zero amount
        (52, 999, -25.00, "USD", "DEBIT",  "2024-05-04 13:00:00", "Amazon",      "COMPLETED"),  # D16 orphan account_id
        (53, 2,   100.00, "USD", "CREDIT", "2999-01-01 00:00:00", "Employer",    "COMPLETED"),  # D17 future occurred_at
        (54, 5,   -10.00, "GBP", "DEBIT",  "2024-05-06 15:00:00", "Netflix",     "UNKNOWN"),    # D18 invalid status
        (55, 3,   -50.00, "USD", "DEBIT",  "2024-05-07 16:00:00", "IKEA",        "COMPLETED"),  # D19 currency != account currency (EUR acct)
        (56, 4,   -75.00, "EUR", "DEBIT",  "2024-05-08 17:00:00", "Zara",        "COMPLETED"),  # D19 currency != account currency (USD acct)
        (57, 1,   200.00, "USD", "DEBIT",  "2024-05-09 18:00:00", "Apple Store", "COMPLETED"),  # D20 DEBIT with positive amount
    ]


# ------------------------------------------------------------- defect manifest
# Each entry: SQL that counts the affected rows, so rows_affected is measured, not asserted.
DEFECTS = [
    dict(id="D01", table="customers", column="email", defect_type="null value",
         dimension="completeness", severity="HIGH",
         description="email is NULL on rows where it is required",
         sql="SELECT COUNT(*) FROM customers WHERE email IS NULL"),
    dict(id="D02", table="customers", column="email", defect_type="duplicate value",
         dimension="uniqueness", severity="HIGH",
         description="same email used by more than one customer",
         sql="SELECT COUNT(*) FROM customers WHERE email IN "
             "(SELECT email FROM customers WHERE email IS NOT NULL GROUP BY email HAVING COUNT(*) > 1)"),
    dict(id="D03", table="customers", column="email", defect_type="invalid format",
         dimension="validity", severity="MEDIUM",
         description="email does not match a basic email pattern",
         sql="SELECT COUNT(*) FROM customers WHERE email IS NOT NULL AND email NOT LIKE '%_@_%._%'"),
    dict(id="D04", table="customers", column="date_of_birth", defect_type="future date",
         dimension="timeliness", severity="HIGH",
         description="date_of_birth is in the future",
         sql=f"SELECT COUNT(*) FROM customers WHERE date_of_birth > '{AS_OF}'"),
    dict(id="D05", table="customers", column="date_of_birth", defect_type="implausible value (under 18)",
         dimension="accuracy", severity="HIGH",
         description="customer is younger than 18 at the reference date",
         sql=f"SELECT COUNT(*) FROM customers WHERE date_of_birth <= '{AS_OF}' "
             f"AND (julianday('{AS_OF}') - julianday(date_of_birth)) / 365.25 < 18"),
    dict(id="D06", table="customers", column="kyc_status", defect_type="value outside allowed set",
         dimension="validity", severity="HIGH",
         description="kyc_status not in PENDING/VERIFIED/REJECTED",
         sql="SELECT COUNT(*) FROM customers WHERE kyc_status NOT IN ('PENDING','VERIFIED','REJECTED')"),
    dict(id="D07", table="customers", column="phone", defect_type="null value",
         dimension="completeness", severity="LOW",
         description="phone is NULL",
         sql="SELECT COUNT(*) FROM customers WHERE phone IS NULL"),
    dict(id="D08", table="accounts", column="customer_id", defect_type="orphan foreign key",
         dimension="integrity", severity="HIGH",
         description="account references a customer_id that does not exist",
         sql="SELECT COUNT(*) FROM accounts a LEFT JOIN customers c ON a.customer_id = c.customer_id "
             "WHERE c.customer_id IS NULL"),
    dict(id="D09", table="accounts", column="currency", defect_type="null value",
         dimension="completeness", severity="MEDIUM",
         description="currency is NULL",
         sql="SELECT COUNT(*) FROM accounts WHERE currency IS NULL"),
    dict(id="D10", table="accounts", column="currency", defect_type="invalid ISO 4217 code",
         dimension="validity", severity="HIGH",
         description="currency is not a known ISO 4217 code",
         sql="SELECT COUNT(*) FROM accounts WHERE currency IS NOT NULL "
             "AND currency NOT IN ('USD','EUR','GBP','CAD','AUD','CHF','JPY')"),
    dict(id="D11", table="accounts", column="balance", defect_type="negative balance on non-overdraft account",
         dimension="consistency", severity="MEDIUM",
         description="CHECKING account with negative balance (overdraft not allowed)",
         sql="SELECT COUNT(*) FROM accounts WHERE account_type = 'CHECKING' AND balance < 0"),
    dict(id="D12", table="accounts", column="opened_date", defect_type="future date",
         dimension="timeliness", severity="MEDIUM",
         description="opened_date is in the future",
         sql=f"SELECT COUNT(*) FROM accounts WHERE opened_date > '{AS_OF}'"),
    dict(id="D13", table="accounts", column="iban", defect_type="duplicate value",
         dimension="uniqueness", severity="HIGH",
         description="same IBAN used by more than one account",
         sql="SELECT COUNT(*) FROM accounts WHERE iban IN "
             "(SELECT iban FROM accounts GROUP BY iban HAVING COUNT(*) > 1)"),
    dict(id="D14", table="transactions", column="amount", defect_type="null value",
         dimension="completeness", severity="HIGH",
         description="amount is NULL",
         sql="SELECT COUNT(*) FROM transactions WHERE amount IS NULL"),
    dict(id="D15", table="transactions", column="amount", defect_type="zero amount",
         dimension="validity", severity="LOW",
         description="amount equals 0",
         sql="SELECT COUNT(*) FROM transactions WHERE amount = 0"),
    dict(id="D16", table="transactions", column="account_id", defect_type="orphan foreign key",
         dimension="integrity", severity="HIGH",
         description="transaction references an account_id that does not exist",
         sql="SELECT COUNT(*) FROM transactions t LEFT JOIN accounts a ON t.account_id = a.account_id "
             "WHERE a.account_id IS NULL"),
    dict(id="D17", table="transactions", column="occurred_at", defect_type="future timestamp",
         dimension="timeliness", severity="MEDIUM",
         description="occurred_at is in the future",
         sql=f"SELECT COUNT(*) FROM transactions WHERE occurred_at > '{AS_OF} 00:00:00'"),
    dict(id="D18", table="transactions", column="status", defect_type="value outside allowed set",
         dimension="validity", severity="MEDIUM",
         description="status not in COMPLETED/PENDING/FAILED",
         sql="SELECT COUNT(*) FROM transactions WHERE status NOT IN ('COMPLETED','PENDING','FAILED')"),
    dict(id="D19", table="transactions", column="currency", defect_type="cross-table inconsistency",
         dimension="consistency", severity="HIGH",
         description="transaction currency differs from its account's currency",
         sql="SELECT COUNT(*) FROM transactions t JOIN accounts a ON t.account_id = a.account_id "
             "WHERE t.currency != a.currency"),
    dict(id="D20", table="transactions", column="amount", defect_type="sign inconsistent with type",
         dimension="validity", severity="MEDIUM",
         description="DEBIT with positive amount or CREDIT with negative amount",
         sql="SELECT COUNT(*) FROM transactions WHERE (transaction_type = 'DEBIT' AND amount > 0) "
             "OR (transaction_type = 'CREDIT' AND amount < 0)"),
    dict(id="D21", table="loans", column="interest_rate", defect_type="out of range",
         dimension="validity", severity="HIGH",
         description="interest_rate outside 0-100",
         sql="SELECT COUNT(*) FROM loans WHERE interest_rate < 0 OR interest_rate > 100"),
    dict(id="D22", table="loans", column="term_months", defect_type="out of range",
         dimension="validity", severity="MEDIUM",
         description="term_months must be a positive integer",
         sql="SELECT COUNT(*) FROM loans WHERE term_months <= 0"),
    dict(id="D23", table="loans", column="principal", defect_type="out of range",
         dimension="validity", severity="HIGH",
         description="principal must be positive",
         sql="SELECT COUNT(*) FROM loans WHERE principal <= 0"),
    dict(id="D24", table="loans", column="customer_id", defect_type="duplicate active loan per customer",
         dimension="uniqueness", severity="MEDIUM",
         description="a customer has more than one ACTIVE loan",
         sql="SELECT COUNT(*) FROM loans l WHERE l.status = 'ACTIVE' AND EXISTS "
             "(SELECT 1 FROM loans l2 WHERE l2.customer_id = l.customer_id AND l2.status = 'ACTIVE' "
             "AND l2.loan_id <> l.loan_id)"),
    dict(id="D25", table="cards", column="expiry_date", defect_type="expired but active",
         dimension="consistency", severity="HIGH",
         description="status ACTIVE although expiry_date is in the past",
         sql=f"SELECT COUNT(*) FROM cards WHERE status = 'ACTIVE' AND expiry_date < '{AS_OF[:7]}'"),
    dict(id="D26", table="cards", column="card_number", defect_type="invalid format",
         dimension="validity", severity="HIGH",
         description="card_number is not exactly 16 digits",
         sql="SELECT COUNT(*) FROM cards WHERE card_number NOT GLOB "
             "'[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'"),
    dict(id="D27", table="cards", column="card_number", defect_type="duplicate value",
         dimension="uniqueness", severity="HIGH",
         description="same card_number issued more than once",
         sql="SELECT COUNT(*) FROM cards WHERE card_number IN "
             "(SELECT card_number FROM cards GROUP BY card_number HAVING COUNT(*) > 1)"),
    dict(id="D28", table="cards", column="account_id", defect_type="orphan foreign key",
         dimension="integrity", severity="HIGH",
         description="card references an account_id that does not exist",
         sql="SELECT COUNT(*) FROM cards c LEFT JOIN accounts a ON c.account_id = a.account_id "
             "WHERE a.account_id IS NULL"),
]


def main():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)

    conn.executemany(
        "INSERT INTO customers VALUES (?,?,?,?,?,?,?)", CUSTOMERS)
    conn.executemany(
        "INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)", ACCOUNTS)
    conn.executemany(
        "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?)",
        clean_transactions() + defective_transactions())
    conn.executemany(
        "INSERT INTO loans VALUES (?,?,?,?,?,?,?)", LOANS)
    conn.executemany(
        "INSERT INTO cards VALUES (?,?,?,?,?,?,?)", CARDS)
    conn.commit()

    with open(MANIFEST_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["defect_id", "table_name", "column_name", "defect_type",
                    "quality_dimension", "severity", "description", "rows_affected"])
        total_rows = 0
        for d in DEFECTS:
            n = conn.execute(d["sql"]).fetchone()[0]
            total_rows += n
            w.writerow([d["id"], d["table"], d["column"], d["defect_type"],
                        d["dimension"], d["severity"], d["description"], n])

    print("Database written to", DB_PATH)
    for t in ["customers", "accounts", "transactions", "loans", "cards"]:
        print(f"  {t:14s} {conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>4} rows")
    print(f"Manifest written to {MANIFEST_PATH}")
    print(f"  {len(DEFECTS)} seeded defects, {total_rows} defective rows in total")
    conn.close()


if __name__ == "__main__":
    main()
