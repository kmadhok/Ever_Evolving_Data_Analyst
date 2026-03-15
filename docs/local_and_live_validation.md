# Local And Live Validation

## Purpose

This document separates:
- local development checks
- live BigQuery validation
- local Airflow validation

## Prerequisites

- `apps/api/.venv` exists
- `apps/ui/node_modules` exists
- local `.env` and `.env.airflow.local` are configured
- live credentials are available for BigQuery work

## Local Validation

### Backend Unit Tests

```bash
make api-test-unit
```

- Expected output: all tests under `apps/api/tests` pass.
- Common failure modes:
  - broken model/response contracts
  - missing venv dependencies

### Frontend Unit Tests

```bash
make ui-test
```

- Expected output: `src/App.test.jsx` passes.
- Common failure modes:
  - changed UI text without updating tests
  - mocked API contract drift

### Frontend Production Build

```bash
make ui-build
```

- Expected output: `apps/ui/dist` emitted successfully.

### Frontend E2E Smoke

```bash
make ui-e2e
```

- Expected output: Playwright passes against the local Vite dev server.
- Coverage:
  - `/de`
  - `/analyst`
  - `/ds`
  - `/consumer`
  - `/ops`

### Local API Startup

```bash
make api-dev
```

- Expected output: Uvicorn starts on `http://localhost:8000`

### Local UI Startup

```bash
make ui-dev
```

- Expected output: Vite starts on `http://localhost:5173`

## Live BigQuery Validation

### Environment Check

```bash
make env-check
```

- Purpose: confirm credentials and local tooling before live queries.

### Apply SQL In Order

```bash
make bq-apply-kalshi-core PROJECT_ID=brainrot-453319
make bq-apply-kalshi-signals PROJECT_ID=brainrot-453319
make bq-apply-odds-core PROJECT_ID=brainrot-453319
```

### Run Live Validation

```bash
make api-test-live DASHBOARD_ID=kalshi_autonomous_v1
```

Checks performed:
- required datasets accessible
- active spec or default spec can be loaded
- active spec validates against live BigQuery views/columns
- default spec validates against live BigQuery views/columns
- one sample tile per role can be queried
- signal feed can be queried

Expected output:
- JSON with `status: passed` or `status: failed`
- persisted record in `kalshi_ops.live_validation_runs`

Common failure modes:
- missing dataset
- missing view
- tile column mismatch
- signal feed view not materialized
- permissions failure on service account

## Local Airflow Validation

### Bring Up Airflow

```bash
make airflow-init
make airflow-up
```

### Trigger DAGs

```bash
make airflow-trigger-kalshi
make airflow-trigger-odds
```

### Observe Logs

```bash
make airflow-logs
```

Expected signals:
- Kalshi DAG writes to raw/staging/core and `kalshi_ops.*`
- Odds DAG writes to raw/staging/core and `odds_ops.*`

Common failure modes:
- Docker env file missing required vars
- invalid `BQ_PROJECT`
- credentials not mounted into the Airflow containers

## Where Results Are Stored

- Live validation runs: `kalshi_ops.live_validation_runs`
- Autonomy runs: `kalshi_ops.autonomy_runs`
- Kalshi quality checks: `kalshi_ops.quality_results`
- Kalshi reports: `kalshi_ops.signal_runs`, `kalshi_ops.market_intelligence_reports`
- Odds quality checks: `odds_ops.quality_results`
- Odds budget decisions: `odds_ops.credit_budget_log`
- Odds run summaries: `odds_ops.run_summary`
