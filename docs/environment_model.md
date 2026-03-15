# Environment Model

## Purpose

This repo is designed for:
- local development on one machine
- one live GCP project for BigQuery-backed validation and operation

It does not assume a separate staging or production repo variant.

## Prerequisites

- one GCP project id
- one service account with access to the required BigQuery datasets
- local credentials file referenced by `GOOGLE_APPLICATION_CREDENTIALS`

## Local Files

- `apps/api/.env`
  Backend settings for FastAPI and BigQuery dataset names.
- `.env.airflow.local`
  Docker Compose env file for local Airflow.
- `credentials_brainrot.json`
  Local credentials file used by the existing setup. It is intentionally not tracked by git.

## Required Variables

### Backend

- `GOOGLE_APPLICATION_CREDENTIALS`
- `GCP_PROJECT_ID` or `gcp_project_id` through `apps/api/.env`
- `BQ_DASH_DATASET`
- `BQ_SIGNAL_DATASET`
- `BQ_OPS_DATASET`
- `BQ_CORE_DATASET`

### Airflow Kalshi

- `BQ_PROJECT`
- `GOOGLE_APPLICATION_CREDENTIALS`
- `KALSHI_API_BASE_URL`
- `KALSHI_READ_RPS`
- `KALSHI_MAX_REQUESTS_PER_RUN`
- `AUTONOMY_DASHBOARD_ID`

### Airflow Odds

- `BQ_PROJECT`
- `ODDS_API_KEY`
- `TARGET_SPORT`
- `TARGET_REGIONS`
- `TARGET_MARKETS`
- `MONTHLY_CREDIT_CAP`
- `CREDIT_RESERVE`

## Dataset Model

Kalshi datasets:
- `kalshi_raw`
- `kalshi_stg`
- `kalshi_core`
- `kalshi_dash`
- `kalshi_signal`
- `kalshi_ops`

Odds datasets:
- `odds_raw`
- `odds_stg`
- `odds_core`
- `odds_ops`

## Expected Credential Capabilities

The service account used for live validation should be able to:
- read all `kalshi_*` datasets
- read all `odds_*` datasets
- insert into `kalshi_ops.autonomy_runs`
- insert into `kalshi_ops.live_validation_runs`
- insert into DAG target raw/staging/ops tables when Airflow is running

## Commands

Run environment validation:

```bash
make env-check
```

Run live validation:

```bash
make api-test-live DASHBOARD_ID=kalshi_autonomous_v1
```

## Expected Outputs

- env check prints resolved settings and credentials path
- live validation prints dataset/spec/query results as JSON

## Common Failure Modes

- `BQ_PROJECT` includes dataset names instead of only project id
- credentials file exists locally but is not readable in Airflow containers
- dataset names in `.env` do not match the live project
- the service account has read access to views but not write access to ops tables

## Logs And Results

- local env checks: stdout
- live validation runs: `kalshi_ops.live_validation_runs`
