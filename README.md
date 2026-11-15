# Contract-Driven Data Quality Monitor

A team of AI agents turns **data contracts (ODCS v3.x YAML)** into trusted, visible,
actionable data quality on a small fictitious banking dataset (SQLite):

```
[User / Scheduler] ──► Orchestrator (deterministic Python, no LLM)
        ├─► A1 Contract Architect   LLM drafts ODCS contract ──► HITL gate (human approves) → active   [cap A]
        ├─► A2 Quality Executor     contract rules → SQL, read-only vs source, runs+history, issues      [cap B]
        ├─► A3 Metrics & Insights   SQL aggregates → static out/dashboard.html (per rule/dimension/severity + trend) [cap C]
        ├─► A4 Notify & Marketplace  owner/steward from contract → out/notifications/*.json + out/marketplace.json  [cap D]
        └─► A5 Issue Resolver (opt)  LLM root-cause suggestion → HITL accept/reject/close → approved write-back [cap E]
```

Agents are ephemeral objects (prompt + tools + bounded loop) and share state only through
the SQLite blackboard (`dq.db`) — see `DESIGN_DECISIONS.md` (ADRs) for every decision and why.

## Run it (end-to-end from a fresh clone)

```bash
pip install -r requirements.txt                 # pyyaml + jsonschema
echo "MISTRAL_API_KEY=sk-..." > .env             # create key at https://console.mistral.ai → workspace → API keys
                                                 # (.env is gitignored; only A1/A5 need it)

python run.py seed                               # build data/banking.db + data/defect_manifest.csv (28 seeded defects)
python run.py init                               # create dq.db (blackboard schema)
python run.py sync                               # validate contracts/*.yaml (ODCS v3.0.0) → register as drafts
python run.py approve --contract customers       # HITL gate (add --auto-approve for demos) — repeat for all 5
python run.py tick                               # run A2→A3→A4 for every contract whose schedule is due
python run.py report                             # detection report vs the defect manifest
```

One-shot demo (sync + auto-approve drafts + full pipeline for all active contracts):

```bash
python run.py demo --auto-approve
```

Other commands: `run --contract X` (manual check run), `draft --table X` (A1 re-drafts a
contract via the LLM), `resolve --issue N` (A5), `dashboard`, `notify`, `compile`
(rule→SQL self-test vs manifest). Full list: `python run.py --help`.

## Outputs (in `out/`)

| File | What |
|---|---|
| `dashboard.html` | capability C: quality per contract/table/rule/dimension/severity + trend over last 10 runs |
| `marketplace.json` | capability D: per-product quality score/status (simulated marketplace catalogue) |
| `notifications/issue_*.json` | capability D: actionable issue mails to the owner+steward from the contract (simulated channel) |
| `detection_report.csv` | Task 1: seeded defect, rule, detected Y/N, severity, rows — paste into the Excel deliverable |
| `detection_report_summary.txt` | found / missed / false positives: **28/28 found, 0 missed, 0 false positives (100%)** |
| `demo/draft_customers_llm.yaml` | a real A1 LLM-drafted contract (kept as demo artifact) |

## Repo layout

```
run.py                 CLI entrypoint (orchestrator)
src/dq/                config, db (source read-only vs state rw), schema.sql, odcs.py (ODCS validator),
                       rules.py (rule→SQL compiler), mistral.py (only LLM client), tools.py (guardrailed
                       tool executors), orchestrator.py, compare_to_manifest.py, agents/{base,a1..a5}
contracts/             ODCS v3.0.0 contracts, one YAML per table (28 rules = 28 seeded defects)
data/                  seed_dataset.py, banking.db (generated), defect_manifest.csv
out/                   generated outputs (gitignored)
DESIGN_DECISIONS.md    the Design Decision Record (ADRs) — read this for the "why"
PHASES.txt             phase log (what was built, in which order, what was verified)
```

## Disclosure (required by the brief)

- **AI coding assistant**: Mistral Vibe wrote the dataset seed script and the agent framework code.
- **Runtime LLM**: only agents A1 (contract drafting) and A5 (issue resolution) call the Mistral API
  (`mistral-small-latest`, tool-calling loop, max 8 iterations); the key is read from `MISTRAL_API_KEY`
  (env or `.env`), never committed. Measured: one A1 draft = 2 calls / 8,029 tokens ≈ $0.0016.
- **Deterministic (no LLM)**: all check execution (contract rules → SQL), scheduling, aggregation,
  dashboard data, notifications, marketplace publishing, detection report. Steady-state scheduled runs
  cost $0 LLM and took 1.04 s wall for 5 contracts / 28 rules on a laptop.
- **Database**: SQLite (`data/banking.db` source, read-only for agents; `dq.db` product state).
  No external services except the Mistral API.
