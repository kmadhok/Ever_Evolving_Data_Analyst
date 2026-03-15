# Data Assets Reference

## Purpose

This file catalogs the primary DAGs, SQL files, datasets, and reporting assets.

## DAGs

### `airflow/dags/kalshi_market_data_autonomous_de_v0.py`

- Purpose: primary Kalshi ingestion and signal/reporting DAG
- Schedule: every 5 minutes
- Upstream API usage:
  - markets
  - events
  - trades
  - orderbooks
- Writes:
  - `kalshi_raw.api_call_log`
  - `kalshi_raw.*_raw`
  - `kalshi_stg.*_stg`
  - `kalshi_core.*`
  - `kalshi_ops.rate_limit_log`
  - `kalshi_ops.quality_results`
  - `kalshi_ops.schema_change_proposals`
  - `kalshi_ops.schema_change_decisions`
  - `kalshi_ops.signal_runs`
  - `kalshi_ops.market_intelligence_reports`
- Quality gates:
  - pre-publish quality checks
  - post-publish quality checks
  - signal quality checks
- Autonomy behavior:
  - proposal generation and governed dashboard autonomy trigger
  - schema changes recorded for review only
- Safe rerun semantics:
  - raw append plus core refresh/merge behavior

### `airflow/dags/odds_api_autonomous_de_v0.py`

- Purpose: companion Odds API ingestion DAG with credit budgeting
- Schedule: hourly
- Upstream API usage:
  - `/sports/` usage heartbeat
  - odds endpoint
  - scores endpoint
- Writes:
  - `odds_raw.api_call_log`
  - `odds_raw.odds_events_raw`
  - `odds_raw.scores_events_raw`
  - `odds_stg.odds_prices_stg`
  - `odds_stg.scores_stg`
  - `odds_core.odds_prices_core`
  - `odds_core.event_outcomes_core`
  - `odds_core.kpi_hourly`
  - `odds_ops.credit_budget_log`
  - `odds_ops.quality_results`
  - `odds_ops.schema_change_proposals`
  - `odds_ops.schema_change_decisions`
  - `odds_ops.run_summary`
- Quality gates:
  - freshness
  - null event id ratio
  - duplicate ratio
- Autonomy behavior:
  - schema changes recorded for review only
- Safe rerun semantics:
  - raw append plus core table refresh

## SQL Files

### `001_baseline_tables.sql`

- Target datasets: `odds_raw`, `odds_stg`, `odds_core`, `odds_ops`
- Purpose: create Odds baseline tables
- Prerequisites: valid project id replacement
- Idempotent: yes

### `002_kalshi_baseline_tables.sql`

- Target datasets: `kalshi_raw`, `kalshi_stg`, `kalshi_core`, `kalshi_ops`
- Purpose: create Kalshi baseline tables including schema governance ops tables
- Idempotent: yes

### `003_kalshi_dashboard_tiles.sql`

- Target datasets: read-only query library
- Purpose: starter tile SQL examples
- Idempotent: not an apply file; do not run as a deployment group

### `004_kalshi_dashboard_views.sql`

- Target datasets: `kalshi_dash`
- Purpose: create dashboard views used by the UI/API
- Prerequisites: `002`
- Idempotent: yes

### `005_kalshi_alert_queries.sql`

- Target datasets: read-only query library
- Purpose: alert query templates
- Idempotent: not an apply file; use manually

### `006_kalshi_autonomy_app_tables.sql`

- Target datasets: `kalshi_ops`
- Purpose: governance tables for spec versions, proposals, decisions, autonomy runs, and live validation runs
- Prerequisites: `002`
- Idempotent: yes

### `007_kalshi_ds_views.sql`

- Target datasets: `kalshi_dash`
- Purpose: DS-specific role views
- Prerequisites: `002`
- Idempotent: yes

### `008_kalshi_event_ingestion_tables.sql`

- Target datasets: `kalshi_raw`, `kalshi_stg`, `kalshi_core`
- Purpose: first-class event ingestion model
- Prerequisites: `002`
- Idempotent: yes

### `009_kalshi_market_intelligence_signals.sql`

- Target datasets: `kalshi_signal`
- Purpose: market-intelligence signal views and unified feed
- Prerequisites: `002`, `004`, `008`
- Idempotent: yes

### `010_kalshi_signal_reporting_tables.sql`

- Target datasets: `kalshi_ops`
- Purpose: signal-run and report persistence
- Prerequisites: `002`
- Idempotent: yes

## API/UI Assets

### `apps/api`

- Purpose: governance API plus CLI for env checks, live validation, and autonomy execution
- Primary outputs:
  - HTTP endpoints
  - CLI JSON output
  - `kalshi_ops.autonomy_runs`
  - `kalshi_ops.live_validation_runs`

### `apps/ui`

- Purpose: role routes plus operator console
- Routes:
  - `/de`
  - `/analyst`
  - `/ds`
  - `/consumer`
  - `/ops`

## Common Failure Modes

- running `003` or `005` as if they were deployment DDL
- applying signal SQL before core/dashboard prerequisites
- pointing Airflow at a project where the required datasets do not exist

## Logs And Results

- Kalshi runtime ops: `kalshi_ops.*`
- Odds runtime ops: `odds_ops.*`
