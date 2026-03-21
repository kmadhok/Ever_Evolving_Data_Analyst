# Phase C (Data Analyst Agent) — Status

## Completed

### 1. `scripts/analyst_agent.py` created
Fully written standalone CLI with:
- `ask` subcommand — natural-language Q&A: question → SQL generation → BigQuery execution → formatted answer
- `report` subcommand — automated reports (daily summary, signal intelligence, market deep-dive)
- Read-only safety rails (`_is_safe_sql` rejects INSERT/UPDATE/DELETE/DROP/CREATE/ALTER)
- Cost guard-rails (500 MB max bytes billed, 1000 row LIMIT enforced)
- Retry logic (auto-retries with error context if first SQL fails)
- Full schema context embedded (all tables, join keys, query patterns from `docs/data_model.md`)
- Uses OpenAI (`gpt-4o`) for SQL generation and answer formatting

### 2. `docs/data_model.md` created
Comprehensive schema reference covering all 5 layers (raw, staging, core, dashboard, signal), join keys, table schemas, common query patterns, and quality gates.

### 3. `apps/api/requirements.txt` updated
Added `openai>=1.0.0` and `tabulate>=0.9.0`.

### 4. Syntax check passes
Script parses cleanly under Python AST validation.

### 5. Venv fixed (Python 3.13)
Recreated `apps/api/.venv` with Python 3.13.11 (Homebrew). All packages install to `python3.13/site-packages` and the default `.venv/bin/python` resolves to 3.13. The previous split-brain issue (pip targeting 3.14, python defaulting to 3.9) is resolved.

### 6. Smoke test passes
`analyst_agent.py --help` runs without import errors; CLI loads cleanly.

---

## Not Yet Done

### 1. `OPENAI_API_KEY` not set
Needs to be added to the shell environment or a `.env` file before the agent can call the LLM.

### 2. Live end-to-end test not run
Requires `OPENAI_API_KEY`. Once set, test with:
```bash
OPENAI_API_KEY=sk-... \
GOOGLE_APPLICATION_CREDENTIALS=./credentials_brainrot.json \
  apps/api/.venv/bin/python scripts/analyst_agent.py ask "How many trades are in the system?"
```

---

## To Finish Phase C

1. Set `OPENAI_API_KEY` in environment
2. Run one successful live test (ask + BigQuery round-trip)
3. Iterate if needed based on live test results
