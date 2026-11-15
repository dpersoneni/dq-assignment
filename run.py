#!/usr/bin/env python3
"""CLI entrypoint for the contract-driven data quality monitor.

Usage examples:
  python run.py seed                  # rebuild data/banking.db + defect manifest
  python run.py init                  # create the state DB (dq.db)
  python run.py draft --table customers
  python run.py approve --contract customers
  python run.py run --contract customers     # manual quality run (capability B)
  python run.py tick                         # run all contracts whose SLA is due
  python run.py dashboard                    # render out/dashboard.html (capability C)
  python run.py report                       # detection report vs defect manifest
  python run.py resolve --issue 3            # capability E (optional)
  python run.py demo                         # full end-to-end flow
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="run.py", description="Contract-driven data quality monitor")
    # accepted both before and after the subcommand; SUPPRESS keeps the subparser
    # from overwriting a value given at the top level
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--auto-approve", action="store_true", default=argparse.SUPPRESS,
                        help="skip interactive HITL prompts (for demos/recordings)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("seed", parents=[common], help="rebuild the source dataset and defect manifest")
    sub.add_parser("init", parents=[common], help="create the state database (dq.db)")
    sub.add_parser("sync", parents=[common], help="validate contracts/*.yaml (ODCS v3.0.0) and register them in dq.db")
    sub.add_parser("compile", parents=[common], help="compile contract rules to SQL and self-test against the defect manifest")
    sub.add_parser("demo", parents=[common], help="run the full flow end-to-end (uses --auto-approve)")
    sub.add_parser("tick", parents=[common], help="run all active contracts whose schedule is due")
    sub.add_parser("dashboard", parents=[common], help="render the static dashboard (Agent A3)")
    sub.add_parser("notify", parents=[common], help="publish notifications + marketplace.json (Agent A4)")
    sub.add_parser("report", parents=[common], help="compare latest run against the defect manifest")

    c = sub.add_parser("draft", parents=[common], help="Agent A1: draft an ODCS contract for a table")
    c.add_argument("--table", required=True)

    c = sub.add_parser("approve", parents=[common], help="HITL gate: approve a draft contract")
    c.add_argument("--contract", required=True)

    c = sub.add_parser("run", parents=[common], help="Agent A2: run quality checks for one contract now")
    c.add_argument("--contract", required=True)

    c = sub.add_parser("resolve", parents=[common], help="Agent A5: explore an issue and propose a fix")
    c.add_argument("--issue", type=int, required=True)

    p.add_argument("--auto-approve", action="store_true",
                   help="skip interactive HITL prompts (for demos/recordings)")
    return p


def main() -> int:
    args = build_parser().parse_args()
    from dq import orchestrator
    return orchestrator.dispatch(args)


if __name__ == "__main__":
    raise SystemExit(main())
