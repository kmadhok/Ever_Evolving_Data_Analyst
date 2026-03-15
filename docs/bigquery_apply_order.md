# BigQuery Apply Order

## Purpose

This is the canonical SQL apply order for the single-project environment.

## Prerequisites

- valid `PROJECT_ID`
- service account with BigQuery DDL permissions
- backend venv installed if you use the helper script

## Exact Commands

### Kalshi Core

```bash
make bq-apply-kalshi-core PROJECT_ID=brainrot-453319
```

Applies:
1. `002_kalshi_baseline_tables.sql`
2. `004_kalshi_dashboard_views.sql`
3. `006_kalshi_autonomy_app_tables.sql`
4. `007_kalshi_ds_views.sql`
5. `008_kalshi_event_ingestion_tables.sql`

### Kalshi Signals

```bash
make bq-apply-kalshi-signals PROJECT_ID=brainrot-453319
```

Applies:
1. `009_kalshi_market_intelligence_signals.sql`
2. `010_kalshi_signal_reporting_tables.sql`

### Odds Core

```bash
make bq-apply-odds-core PROJECT_ID=brainrot-453319
```

Applies:
1. `001_baseline_tables.sql`

## Files Not Included In Automated Apply

- `003_kalshi_dashboard_tiles.sql`
  Query library only
- `005_kalshi_alert_queries.sql`
  Alert query templates only

## Expected Outputs

- each SQL file is announced before execution
- BigQuery job completes without error

## Common Failure Modes

- applying signals before baseline datasets/views exist
- using a mismatched project id
- credentials do not allow DDL

## Rollback Guidance

- table/view creation files are intended to be idempotent
- for view regressions, re-apply the prior known-good SQL file content
- for governance tables, avoid destructive rollback unless there is an explicit data migration plan

## Where Logs And Results Are Stored

- helper script output: stdout
- resulting objects: the live BigQuery project datasets
