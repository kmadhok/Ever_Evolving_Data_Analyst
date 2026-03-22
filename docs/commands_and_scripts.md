# Commands And Scripts

## Purpose

This file is the repo-level inventory for:
- Make targets
- helper scripts in `scripts/`
- backend CLI commands
- frontend test commands

Use this before reading source code.

## Prerequisites

- Backend virtualenv created in `apps/api/.venv`
- Frontend dependencies installed in `apps/ui/node_modules`
- `GOOGLE_APPLICATION_CREDENTIALS` or `apps/api/.env` configured for live BigQuery work
- Docker available for legacy Airflow commands
- `sudo` available if installing the Linux `systemd` scheduler service

## Make Targets

### `make airflow-init`

- Purpose: Initialize the local Airflow metadata DB and admin account.
- Scope: local-only
- Command:

```bash
make airflow-init
```

- Expected output: `docker compose` completes the `airflow-init` service.
- Common failure modes:
  - Docker daemon not running
  - invalid `.env.airflow.local`
- Logs: Docker compose output

### `make airflow-up`

- Purpose: Start Airflow webserver and scheduler.
- Scope: local-only
- Expected output: containers start and `make airflow-ps` shows both services.
- Logs: Docker compose output and `make airflow-logs`

### `make airflow-down`

- Purpose: Stop local Airflow services.
- Scope: local-only

### `make airflow-logs`

- Purpose: Tail scheduler and webserver logs.
- Scope: local-only

### `make airflow-ps`

- Purpose: Show local Airflow container status.
- Scope: local-only

### `make api-setup`

- Purpose: Create the backend venv and install runtime dependencies.
- Scope: local-only

### `make scheduler-smoke`

- Purpose: Validate the scheduler runtime before manual runs or service install.
- Scope: local-only
- Backing script: `scripts/scheduler_smoke_check.sh`
- Checks:
  - `apps/api/.venv/bin/python` exists and is executable
  - expected credential and Kalshi key files exist
  - direct imports required by the scheduler stack succeed
  - `scripts/scheduler.py` imports successfully without starting the scheduler

### `make scheduler-install`

- Purpose: Generate, install, and enable a machine-specific Linux `systemd` unit for the scheduler.
- Scope: local-only
- Backing script: `scripts/install_scheduler_service.sh`
- Notes:
  - runs the same checks as `make scheduler-smoke` before installing
  - uses the current checkout path and current user
  - uses `GOOGLE_APPLICATION_CREDENTIALS` and `KALSHI_PRIVATE_KEY_PATH` from the current shell if set, otherwise falls back to repo-root defaults

### `make api-dev`

- Purpose: Run FastAPI locally with reload.
- Scope: local-only
- Expected output: Uvicorn listens on `http://localhost:8000`

### `make api-test-unit`

- Purpose: Run backend `unittest` coverage.
- Scope: local-only
- Expected output: test discovery under `apps/api/tests` passes.
- Common failure modes:
  - venv missing
  - dependency mismatch in `apps/api/.venv`

### `make api-test-live DASHBOARD_ID=kalshi_autonomous_v1`

- Purpose: Run live BigQuery/dashboard smoke validation.
- Scope: live-environment
- Backing script: `scripts/live_bigquery_smoke_checks.sh`
- Expected output: JSON payload with `status: passed` or `status: failed`
- Logs/results: stdout plus `kalshi_ops.live_validation_runs` when write mode is enabled

### `make ui-dev`

- Purpose: Run the Vite dev server.
- Scope: local-only
- Expected output: frontend served on `http://localhost:5173`

### `make ui-test`

- Purpose: Run Vitest component coverage.
- Scope: local-only
- Expected output: `src/App.test.jsx` passes

### `make ui-build`

- Purpose: Run a production frontend build.
- Scope: local-only
- Expected output: `apps/ui/dist`

### `make ui-e2e`

- Purpose: Run Playwright smoke coverage for the role routes.
- Scope: local-only
- Expected output: `tests/e2e/dashboard.spec.js` passes
- Common failure modes:
  - browser install missing
  - dev server port collision

### `make bq-apply-kalshi-core PROJECT_ID=<gcp-project>`

- Purpose: Apply Kalshi baseline/dashboard/autonomy SQL in dependency order.
- Scope: live-environment
- Backing script: `scripts/apply_bigquery_sql.py --group kalshi-core`

### `make bq-apply-kalshi-signals PROJECT_ID=<gcp-project>`

- Purpose: Apply Kalshi signal/reporting SQL.
- Scope: live-environment
- Backing script: `scripts/apply_bigquery_sql.py --group kalshi-signals`

### `make bq-apply-kalshi-ws PROJECT_ID=<gcp-project>`

- Purpose: Apply Kalshi WebSocket ingestion tables (raw, staging, ops).
- Scope: live-environment
- Backing script: `scripts/apply_bigquery_sql.py --group kalshi-ws`
- SQL file: `sql/bigquery/011_kalshi_ws_tables.sql`

### `make bq-apply-odds-core PROJECT_ID=<gcp-project>`

- Purpose: Apply Odds baseline datasets/tables SQL.
- Scope: live-environment
- Backing script: `scripts/apply_bigquery_sql.py --group odds-core`

### `make airflow-trigger-kalshi`

- Purpose: Trigger the Kalshi DAG in the local Airflow stack.
- Scope: local-only
- Backing script: `scripts/trigger_airflow_dag.sh`

### `make airflow-trigger-odds`

- Purpose: Trigger the Odds DAG in the local Airflow stack.
- Scope: local-only
- Backing script: `scripts/trigger_airflow_dag.sh`

### `make airflow-trigger-kalshi-ws`

- Purpose: Trigger the Kalshi WebSocket DAG in the local Airflow stack.
- Scope: local-only
- Backing script: `scripts/trigger_airflow_dag.sh`
- Note: This DAG is **paused** in favor of the standalone script `scripts/kalshi_ws_ingest.py`.

### `make airflow-unpause-kalshi-ws`

- Purpose: Unpause the Kalshi WebSocket DAG.
- Scope: local-only
- Note: Only needed if re-enabling the Airflow-based WS ingestion.

### `make env-check`

- Purpose: Run repo-level environment validation.
- Scope: local-only
- Backing script: `scripts/env_check.sh`

## Helper Scripts

### `scripts/env_check.sh`

- Purpose: Validate API settings, credentials, Node.js, and npm.
- Exact command:

```bash
./scripts/env_check.sh
```

- Expected output: JSON from `python -m app.cli env-check`, then Node and npm versions.
- Common failure modes:
  - `apps/api/.venv` missing
  - credentials path missing
  - Node not installed

### `scripts/scheduler_smoke_check.sh`

- Purpose: Validate the local scheduler runtime before starting the long-running process or installing the Linux service.
- Exact command:

```bash
./scripts/scheduler_smoke_check.sh
```

- Expected output: `scheduler smoke check passed`
- Common failure modes:
  - `apps/api/.venv` missing or copied from another OS
  - missing `credentials_brainrot.json`
  - missing `kalshi_private_key.pem`
  - missing direct runtime dependency in `apps/api/.venv`

### `scripts/install_scheduler_service.sh`

- Purpose: Generate and install a machine-specific `systemd` unit for `scripts/run_scheduler.sh`
- Exact command:

```bash
./scripts/install_scheduler_service.sh
```

- Behavior:
  - runs the scheduler smoke check
  - renders a unit file using the current repo path and current user
  - installs the generated unit into `/etc/systemd/system/`
  - reloads `systemd`
  - enables and starts `ever-evolving-scheduler.service`
- Common failure modes:
  - `sudo` access missing
  - smoke check failure
  - invalid or missing credentials paths

### `scripts/run_scheduler.sh`

- Purpose: Start `scripts/scheduler.py` using the repo venv when available, or fall back to `python3` if it already has APScheduler installed
- Exact command:

```bash
./scripts/run_scheduler.sh
```

- Expected output: scheduler startup logs
- Common failure modes:
  - repo venv missing or broken
  - APScheduler missing from the selected Python runtime

### `scripts/apply_bigquery_sql.py`

- Purpose: Apply ordered SQL groups to BigQuery.
- Exact command:

```bash
./apps/api/.venv/bin/python ./scripts/apply_bigquery_sql.py --project-id brainrot-453319 --group kalshi-core
```

- Supported groups:
  - `kalshi-core`
  - `kalshi-signals`
  - `odds-core`
- Expected output: one `Applying ...` line per SQL file.
- Common failure modes:
  - missing credentials
  - SQL references datasets/tables that do not yet exist
  - project id mismatch

### `scripts/live_bigquery_smoke_checks.sh`

- Purpose: Run backend live validation without starting HTTP.
- Exact command:

```bash
./scripts/live_bigquery_smoke_checks.sh kalshi_autonomous_v1
```

- Expected output: JSON validation result
- Logs/results: stdout and optionally `kalshi_ops.live_validation_runs`

### `scripts/trigger_airflow_dag.sh`

- Purpose: Trigger a DAG inside the local Docker Airflow stack.
- Exact command:

```bash
./scripts/trigger_airflow_dag.sh kalshi_market_data_autonomous_de_v0
```

- Expected output: Airflow CLI trigger confirmation
- Common failure modes:
  - Airflow containers not running
  - wrong `ENV_FILE`

### `scripts/kalshi_ws_ingest.py`

- Purpose: Standalone WebSocket ingestion for Kalshi market data. Fetches top markets from the Kalshi REST API (no BigQuery or Airflow dependency), connects to the authenticated WebSocket, collects trade/ticker/orderbook messages for a configurable listen window, and persists to BigQuery (raw, staging, and ops tables).
- Replaces: `airflow/dags/kalshi_ws_realtime_v0.py` (Airflow DAG is now paused)
- Exact command:

```bash
GOOGLE_APPLICATION_CREDENTIALS=./credentials_brainrot.json \
KALSHI_PRIVATE_KEY_PATH=./kalshi_private_key.pem \
  ./apps/api/.venv/bin/python scripts/kalshi_ws_ingest.py
```

- Configuration (env vars, loaded from `.env.kalshi.local` > `.env.airflow.local` > `.env.kalshi.local.example`):
  - `BQ_PROJECT` (default: `brainrot-453319`)
  - `KALSHI_API_KEY` (required)
  - `KALSHI_PRIVATE_KEY_PATH` (path to RSA PEM file, default: `./kalshi_private_key.pem`)
  - `KALSHI_API_BASE_URL` (default: `https://api.elections.kalshi.com/trade-api/v2`)
  - `KALSHI_WS_ENDPOINT` (default: `wss://api.elections.kalshi.com/trade-api/ws/v2`)
  - `KALSHI_WS_LISTEN_SECONDS` (default: `240` = 4 minutes)
  - `KALSHI_WS_MAX_MARKETS` (default: `25`)
  - `KALSHI_WS_RECONNECT_DELAY_SECONDS` (default: `2`)
  - `KALSHI_WS_MAX_RECONNECTS` (default: `3`)
- Pipeline steps:
  1. Fetch top N open markets from Kalshi REST API (`GET /markets?status=open`)
  2. Connect to WebSocket with RSA-PSS SHA256 authentication
  3. Subscribe to `ticker`, `trade`, `orderbook_delta` channels
  4. Listen for 240 seconds, collect messages
  5. Persist raw messages to `kalshi_raw.ws_trades_raw`, `ws_ticker_raw`, `ws_orderbook_snapshots_raw`
  6. Transform into typed staging tables `kalshi_stg.ws_trades_stg`, `ws_ticker_stg`
  7. Log session metadata to `kalshi_ops.ws_connection_log`
- Expected output: log lines showing market resolution, connection, message counts, and BQ persistence
- Common failure modes:
  - `KALSHI_API_KEY` not set
  - Private key PEM file not found
  - `GOOGLE_APPLICATION_CREDENTIALS` not set or invalid
  - `websocket-client` not installed in venv (run `pip install websocket-client`)

### `scripts/analyst_agent.py`

- Purpose: Natural-language AI agent for querying BigQuery data on Kalshi prediction markets using OpenAI GPT-4o. Supports ad-hoc questions and predefined reports.
- Exact commands:

```bash
# Ad-hoc question
python scripts/analyst_agent.py ask "What are the top 10 markets by volume?"

# Predefined reports
python scripts/analyst_agent.py report --type daily
python scripts/analyst_agent.py report --type signals
python scripts/analyst_agent.py report --type market --ticker KXBTC
python scripts/analyst_agent.py report --type daily --output reports/daily.md
```

- Configuration:
  - `OPENAI_API_KEY` (required)
  - `GOOGLE_APPLICATION_CREDENTIALS` (required)
  - `GCP_PROJECT_ID` (default: `brainrot-453319`)
- Safety: 500MB max bytes billed, 1000 row limit
- Common failure modes:
  - `OPENAI_API_KEY` not set
  - BigQuery credentials missing

## Backend CLI

Run from `apps/api`:

### `python -m app.cli env-check`

- Purpose: Print backend config and credential discovery state.
- Output: JSON

### `python -m app.cli validate-live --dashboard-id kalshi_autonomous_v1`

- Purpose: Validate datasets, views, active/default spec, per-role sample tiles, and signal feed accessibility.
- Output: JSON plus optional persisted validation row

### `python -m app.cli governance-run --dashboard-id kalshi_autonomous_v1`

- Purpose: Run the autonomy loop without HTTP.
- Output: JSON `AutonomyRunResponse`

### `python -m app.cli governance-summary --dashboard-id kalshi_autonomous_v1`

- Purpose: Print summary counts and timestamps for operator review.
- Output: JSON `GovernanceSummaryResponse`

## Frontend Commands

Run from `apps/ui`:

- `npm run dev`
- `npm run build`
- `npm run test -- --run`
- `npm run test:e2e`

## Logs And Results

- Backend unit results: stdout
- Frontend unit results: stdout
- Frontend E2E results: stdout
- Live validation: stdout and `kalshi_ops.live_validation_runs`
- Autonomy cycle CLI/API runs: stdout and `kalshi_ops.autonomy_runs`
- Kalshi DAG quality results: `kalshi_ops.quality_results`
- Kalshi signal reporting: `kalshi_ops.signal_runs`, `kalshi_ops.market_intelligence_reports`
- Kalshi WebSocket session logs: `kalshi_ops.ws_connection_log`
- Odds budget decisions: `odds_ops.credit_budget_log`
- Odds run summaries: `odds_ops.run_summary`

## Schedules

### Standalone Scripts (run via scheduler or manually)

All pipelines are standalone Python scripts. No Airflow required.

| Script | Schedule | Description |
|---|---|---|
| `scripts/kalshi_market_ingest.py` | `*/5 * * * *` (every 5 min) | REST polling for markets, events, trades, orderbooks. Persists raw, builds staging, publishes core, computes KPIs, backfills event titles. |
| `scripts/kalshi_ws_ingest.py` | `*/5 * * * *` (every 5 min) | WebSocket real-time data ingestion (ticker, trade, orderbook_delta). |
| `scripts/kalshi_signals.py` | `*/5 * * * *` (every 5 min) | Signal run summaries, market intelligence reports, quality checks on core/signal tables. Runs after market ingest. |
| `scripts/odds_api_ingest.py` | `@hourly` | Credit-aware Odds API ingestion. Fetches odds/scores, persists raw, builds staging, publishes core, computes KPIs. |
| `scripts/scheduler.py` | Long-running | APScheduler process that runs all of the above on their intervals. |
| `scripts/analyst_agent.py` | Manual / on-demand | AI-powered data analysis and reporting. |
| `scripts/apply_bigquery_sql.py` | Manual | Schema migrations. |

### Running the Scheduler

The recommended way to run all pipelines is via `scripts/scheduler.py`:

```bash
GOOGLE_APPLICATION_CREDENTIALS=./credentials_brainrot.json \
KALSHI_PRIVATE_KEY_PATH=./kalshi_private_key.pem \
  ./apps/api/.venv/bin/python scripts/scheduler.py
```

This starts an APScheduler process that runs each script as a subprocess on its configured interval:
- `kalshi_market_ingest.py` at `:00`, `:05`, `:10`, ... (every 5 min)
- `kalshi_ws_ingest.py` at `:01`, `:06`, `:11`, ... (every 5 min, offset by 1 min)
- `kalshi_signals.py` at `:03`, `:08`, `:13`, ... (every 5 min, offset by 3 min)
- `odds_api_ingest.py` at `:00` each hour

Press Ctrl+C to stop. Each script has a 10-minute subprocess timeout.

### Linux Service Install

The recommended Linux production-style setup is:

```bash
make api-setup
make scheduler-smoke
make scheduler-install
```

Then monitor with:

```bash
sudo systemctl status ever-evolving-scheduler --no-pager
journalctl -u ever-evolving-scheduler -f
```

### Running Scripts Individually

Each script can also be run standalone:

```bash
# Kalshi market data
GOOGLE_APPLICATION_CREDENTIALS=./credentials_brainrot.json \
  ./apps/api/.venv/bin/python scripts/kalshi_market_ingest.py

# Kalshi WebSocket
GOOGLE_APPLICATION_CREDENTIALS=./credentials_brainrot.json \
KALSHI_PRIVATE_KEY_PATH=./kalshi_private_key.pem \
  ./apps/api/.venv/bin/python scripts/kalshi_ws_ingest.py

# Kalshi signals
GOOGLE_APPLICATION_CREDENTIALS=./credentials_brainrot.json \
  ./apps/api/.venv/bin/python scripts/kalshi_signals.py

# Odds API
GOOGLE_APPLICATION_CREDENTIALS=./credentials_brainrot.json \
  ./apps/api/.venv/bin/python scripts/odds_api_ingest.py
```

### Cron Setup (alternative to scheduler.py)

If you prefer cron over the Python scheduler:

```bash
crontab -e
```

Add:

```cron
# Kalshi market data (every 5 min)
*/5 * * * * cd /path/to/Ever_Evolving_Software && GOOGLE_APPLICATION_CREDENTIALS=./credentials_brainrot.json ./apps/api/.venv/bin/python scripts/kalshi_market_ingest.py >> /tmp/kalshi_market.log 2>&1

# Kalshi WebSocket (every 5 min)
*/5 * * * * cd /path/to/Ever_Evolving_Software && GOOGLE_APPLICATION_CREDENTIALS=./credentials_brainrot.json KALSHI_PRIVATE_KEY_PATH=./kalshi_private_key.pem ./apps/api/.venv/bin/python scripts/kalshi_ws_ingest.py >> /tmp/kalshi_ws.log 2>&1

# Kalshi signals (every 5 min, offset by 3 min)
3-59/5 * * * * cd /path/to/Ever_Evolving_Software && GOOGLE_APPLICATION_CREDENTIALS=./credentials_brainrot.json ./apps/api/.venv/bin/python scripts/kalshi_signals.py >> /tmp/kalshi_signals.log 2>&1

# Odds API (hourly)
0 * * * * cd /path/to/Ever_Evolving_Software && GOOGLE_APPLICATION_CREDENTIALS=./credentials_brainrot.json ./apps/api/.venv/bin/python scripts/odds_api_ingest.py >> /tmp/odds.log 2>&1
```

Notes:
- The WS script listens for 240 seconds (4 min), then writes to BQ and exits before the next 5-minute window.
- Logs append to `/tmp/*.log`. Rotate or redirect as needed.
- Scripts load env vars from `.env.kalshi.local` and `.env.airflow.local` automatically, but `GOOGLE_APPLICATION_CREDENTIALS` must be set explicitly for cron.

## Ingestion Script Details

### `scripts/kalshi_market_ingest.py`

- Replaces: `airflow/dags/kalshi_market_data_autonomous_de_v0.py`
- Schedule: Every 5 minutes
- Pipeline steps: plan rate limits > record plan > fetch markets + trades + events + orderbooks > persist raw > build staging > quality gates (warn, don't halt) > publish core > backfill event titles > compute KPIs
- BigQuery tables: `kalshi_raw.*`, `kalshi_stg.*`, `kalshi_core.*`, `kalshi_ops.*`
- Configuration: `KALSHI_API_BASE_URL`, `KALSHI_USAGE_TIER`, `KALSHI_READ_RPS`, `KALSHI_MAX_REQUESTS_PER_RUN`, and all quality threshold env vars

### `scripts/kalshi_signals.py`

- Replaces: Signal/report tasks from `kalshi_market_data_autonomous_de_v0.py`
- Schedule: Every 5 minutes (after market ingest)
- Pipeline steps: post-publish quality checks > signal run summary > market intelligence reports > signal quality checks
- BigQuery tables: `kalshi_signal.*`, `kalshi_ops.signal_runs`, `kalshi_ops.market_intelligence_reports`, `kalshi_ops.quality_results`

### `scripts/odds_api_ingest.py`

- Replaces: `airflow/dags/odds_api_autonomous_de_v0.py`
- Schedule: Hourly
- Pipeline steps: usage heartbeat > credit budget planning > record decision > fetch odds + scores > persist raw > build staging > quality gates (warn, don't halt) > publish core > compute KPIs > emit run summary
- BigQuery tables: `odds_raw.*`, `odds_stg.*`, `odds_core.*`, `odds_ops.*`
- Configuration: `ODDS_API_KEY`, `TARGET_SPORT`, `MONTHLY_CREDIT_CAP`, `CREDIT_RESERVE`, `MAX_PAID_CYCLES_PER_DAY`

### `scripts/scheduler.py`

- Purpose: APScheduler-based process that runs all ingestion scripts as subprocesses on their intervals
- Dependency: `apscheduler` (installed in venv)
- Features: subprocess isolation (each script runs independently), 10-minute timeout per script, graceful shutdown on SIGINT/SIGTERM, logging of schedule events and exit codes
- Companion scripts:
  - `scripts/run_scheduler.sh`
  - `scripts/scheduler_smoke_check.sh`
  - `scripts/install_scheduler_service.sh`
- Note: This replaces Docker Airflow entirely. No database, no web UI, just a Python process.

## Airflow DAGs (archived, kept for reference)

The Airflow DAGs in `airflow/dags/` are kept for reference but are no longer the primary execution path. All pipelines now run as standalone scripts via `scripts/scheduler.py`.

| DAG | Replaced By |
|---|---|
| `kalshi_market_data_autonomous_de_v0` | `scripts/kalshi_market_ingest.py` + `scripts/kalshi_signals.py` |
| `odds_api_autonomous_de_v0` | `scripts/odds_api_ingest.py` |
| `kalshi_ws_realtime_v0` | `scripts/kalshi_ws_ingest.py` (already standalone) |
