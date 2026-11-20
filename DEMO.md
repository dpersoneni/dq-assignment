# Live demo runbook (45-min slot: ~10–12 min demo + Q&A)

Everything runs from the repo root: `delen-dq-assignment/`. Terminal font up, other apps closed.

## 0. Prep (day before, ~5 min)

```bash
cd delen-dq-assignment
pip install -r requirements.txt                 # done already, idempotent
# .env with MISTRAL_API_KEY must exist (only A1/A5 live calls need it)
# reset to a clean demo state:
rm -f dq.db data/banking.db
python run.py seed && python run.py init && python run.py sync
for c in customers accounts transactions loans cards; do python run.py approve --contract $c --auto-approve >/dev/null; done
cp -r contracts /tmp/contracts_demo_backup       # in case the live A1 draft overwrites one
python run.py demo --auto-approve                 # dry run: everything must work, ~1-2 s
python run.py report                              # expect 100.0% (28/28)
```

Have a browser tab ready for `out/dashboard.html` (open it once so it's loaded; refresh during the demo).

## 1. Live demo script

| # | Command | What to say | Time |
|---|---|---|---|
| 1 | (show `DESIGN_DECISIONS.md` §3 / README diagram) | "Orchestrator is deterministic Python; 5 agents; LLM only in A1 contract drafting and A5 issue resolving — everything else is code, by design." | 1 min |
| 2 | `python run.py compile` | "Contracts drive all checks: 28 ODCS rules compile to read-only SQL; self-test against the seeded defect manifest: 36 vs 36 rows OK." | 30 s |
| 3 | `cat contracts/transactions.yaml` | "An ODCS v3.0.0 contract: schema, owner/steward in `team`, SLA schedule (5 min), quality rules. Validated against the official ODCS schema at `sync`." | 1 min |
| 4 | `python run.py draft --table customers` | "A1 asks the LLM to draft a fresh contract from the real schema; we normalize, validate against ODCS, and only then save as draft. A human must approve before it runs." (needs API key; ~10–20 s) | 1 min |
| 5 | `python run.py sync && python run.py approve --contract customers --auto-approve` | "Editing a contract flipped it back to draft (re-approval guardrail); approve makes it active." | 30 s |
| 6 | `python run.py tick` (set all due first if needed: see note) or `python run.py demo --auto-approve` | "Scheduler tick / full pipeline: A2 runs every active contract's rules read-only, writes runs+results+deduped issues; A3 renders the dashboard; A4 notifies + publishes marketplace." | 1 min |
| 7 | browser: refresh `out/dashboard.html` | "Capability C: per contract/table/rule/dimension/severity, scores, and the trend over the last runs." | 1 min |
| 8 | `cat out/marketplace.json` | "Capability D: each data product gets a quality score/status on the marketplace catalogue." | 30 s |
| 9 | `cat out/notifications/issue_1.json` | "Capability D: actionable notification addressed to the owner+steward taken from the contract — the agent cannot misroute." | 30 s |
| 10 | `python run.py report` | "Task 1: detected vs seeded manifest — 28/28 found, 0 missed, 0 false positives = 100%." | 30 s |
| 11 | `python run.py resolve --issue 2` (WITHOUT --auto-approve) | "Capability E: A5 (LLM) reads the issue + offending rows read-only, proposes a root cause and fix as JSON; the HITL gate blocks until a human chooses — type `1` to accept live. If a write-back is accepted, the orchestrator executes it in one transaction and re-runs the contract to confirm the rule now passes." | 2 min |

Note for step 6: after approvals, `next_run_due` is in the future; either wait or force it:
```bash
python -c "import sys; sys.path.insert(0,'src'); from dq import db; c=db.state(); c.execute(\"UPDATE contracts SET next_run_due='2020-01-01 00:00:00' WHERE status='active'\"); c.commit()"
python run.py tick
```

## 2. Fallbacks (if something breaks live)

- **API/LLM down**: skip step 4 (show the saved LLM draft `out/demo/draft_customers_llm.yaml` instead) and skip step 11's LLM (A5 falls back to a deterministic template suggestion). Steps 2,3,5,6,7,8,9,10 need no API.
- **Terminal/output glitch**: `out/demo/demo_transcript.txt` is a full pre-recorded transcript of the whole flow; `out/` already contains dashboard, marketplace, notifications, detection report.
- **State messed up mid-demo**: reset is one line (see prep): `rm -f dq.db data/banking.db && python run.py seed && python run.py init && python run.py sync && ...` — or `git checkout -- .` won't restore DBs, so reseed.
- **Cost question**: `python -c "import sys; sys.path.insert(0,'src'); from dq import db; print(db.state().execute('SELECT COUNT(*), SUM(total_tokens) FROM llm_calls').fetchone())"` → measured ~8k tokens ≈ $0.0016 per contract draft; scheduled runs cost $0 LLM.

## 3. Q&A cheat sheet (answers live in `DESIGN_DECISIONS.md`)

- Why agents vs one agent / plain script → §7 + ADR-04/06
- Where deterministic code instead of LLM → §7; `grep -r mistral src/dq` shows the LLM client is only used via `agents/base.py`
- Human-in-the-loop points → ADR-07/17 (contract approval, issue resolution write-back)
- KPIs (measured) → §5: 100% detection (28/28, 0 FP), 5/5 contracts active, 1.04 s pipeline, $0 steady-state LLM cost
- What breaks at 500 contracts / 50 sources → §5
- Safeguards: invented rules / misrouting / sensitive data → §7
- MCP considered → ADR-18
