"""Central configuration: paths, model, constants. Secrets come from the environment only (ADR-12)."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
SOURCE_DB = DATA_DIR / "banking.db"          # source data — agents open this READ-ONLY
STATE_DB = ROOT / "dq.db"                     # product state / blackboard — read-write
CONTRACTS_DIR = ROOT / "contracts"
OUT_DIR = ROOT / "out"
SCHEMA_SQL = Path(__file__).with_name("schema.sql")

def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from a .env file (gitignored). Real environment wins."""
    if not path.exists():
        return
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16")          # PowerShell/Notepad UTF-16 files
    else:
        text = raw.decode("utf-8-sig", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "")
MISTRAL_BASE_URL = os.environ.get("MISTRAL_BASE_URL", "https://api.mistral.ai/v1")
MODEL = os.environ.get("DQ_MODEL", "mistral-small-latest")
MAX_TOOL_ITERATIONS = 8

# Fixed as-of date for time-based checks against the seeded dataset (ADR-03),
# so the detection report is reproducible. Production runs would use the run timestamp.
AS_OF = "2025-06-30"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    CONTRACTS_DIR.mkdir(exist_ok=True)
    OUT_DIR.mkdir(exist_ok=True)
