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
- Docker available for Airflow commands

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
- Odds budget decisions: `odds_ops.credit_budget_log`
- Odds run summaries: `odds_ops.run_summary`
