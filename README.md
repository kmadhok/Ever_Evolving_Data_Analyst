# Ever Evolving Software

Live-operable beta workspace for:
- Kalshi market ingestion and signal reporting
- Odds API ingestion with credit-aware budgeting
- FastAPI governance services for dashboard spec autonomy
- React role-based dashboards and operator console

## What Lives Here

- `airflow/dags/kalshi_market_data_autonomous_de_v0.py`
  Primary Kalshi ingestion DAG. Persists raw/staging/core data, quality results, signal runs, market-intelligence reports, and governed schema-review records.
- `airflow/dags/odds_api_autonomous_de_v0.py`
  Companion Odds API DAG. Persists budget decisions, raw payloads, staging/core tables, quality results, schema-review records, and run summaries.
- `apps/api`
  FastAPI backend for spec serving, usage logging, proposal generation, governance decisions, apply, rollback, CLI operations, and live validation.
- `apps/ui`
  Vite/React UI for `/de`, `/analyst`, `/ds`, `/consumer`, and `/ops`.
- `sql/bigquery`
  Ordered SQL source of truth for datasets, tables, views, and reporting assets.
- `scripts`
  Repo-level helpers for env checks, BigQuery apply flows, live validation, and local Airflow DAG triggers.

## Quick Start

### 1. Backend

```bash
cd /Users/kanumadhok/Downloads/code/Ever_Evolving_Software/apps/api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Run the local backend:

```bash
make api-dev
```

Run backend unit tests:

```bash
make api-test-unit
```

### 2. Frontend

```bash
cd /Users/kanumadhok/Downloads/code/Ever_Evolving_Software/apps/ui
npm install
```

Run the local frontend:

```bash
make ui-dev
```

Run frontend validation:

```bash
make ui-test
make ui-build
make ui-e2e
```

### 3. Repo-Level Checks

```bash
make env-check
```

This checks:
- API settings and credential discovery
- Node.js availability
- npm availability

### 4. BigQuery SQL Apply

Apply SQL groups in order:

```bash
make bq-apply-kalshi-core PROJECT_ID=brainrot-453319
make bq-apply-kalshi-signals PROJECT_ID=brainrot-453319
make bq-apply-odds-core PROJECT_ID=brainrot-453319
```

Run live BigQuery smoke validation:

```bash
make api-test-live DASHBOARD_ID=kalshi_autonomous_v1
```

### 5. Airflow

```bash
make airflow-init
make airflow-up
make airflow-trigger-kalshi
make airflow-trigger-odds
```

## Key Commands

- `make api-test-unit`
  Runs backend `unittest` coverage for policy, diffing, apply/rollback, CLI, and live-validation helpers.
- `make api-test-live`
  Runs live BigQuery validation through `python -m app.cli validate-live`.
- `make ui-test`
  Runs Vitest component tests.
- `make ui-e2e`
  Runs Playwright smoke coverage across the main role routes.
- `make bq-apply-kalshi-core`
  Applies Kalshi core/dashboard/autonomy SQL in dependency order.
- `make bq-apply-kalshi-signals`
  Applies Kalshi signal and reporting SQL in dependency order.
- `make bq-apply-odds-core`
  Applies Odds baseline datasets/tables.

## Backend Interfaces

Stable API endpoints:
- `GET /health`
- `GET /v1/dashboard/spec`
- `POST /v1/dashboard/spec`
- `POST /v1/dashboard/spec/validate`
- `GET /v1/dashboard/tile/{tile_id}`
- `GET /v1/signals/feed`
- `POST /v1/usage/events`
- `GET /v1/agent/proposals`
- `GET /v1/governance/proposals`
- `POST /v1/governance/proposals/{proposal_id}/decision`
- `GET /v1/governance/spec-versions`
- `GET /v1/governance/summary`
- `POST /v1/governance/run`
- `POST /v1/governance/apply`
- `POST /v1/governance/rollback`

CLI entrypoints:
- `python -m app.cli env-check`
- `python -m app.cli validate-live`
- `python -m app.cli governance-run`
- `python -m app.cli governance-summary`

## Dashboard Routes

- `/de`
- `/analyst`
- `/ds`
- `/consumer`
- `/ops`

The operator console shows:
- active and previous spec versions
- proposal backlog and decision counts
- last autonomy run timestamp
- last successful live validation timestamp
- API base and spec source metadata

## Documentation Map

- `docs/commands_and_scripts.md`
  Inventory of every Make target, helper script, CLI command, and test entrypoint.
- `docs/local_and_live_validation.md`
  Exact local checks versus live-environment checks.
- `docs/environment_model.md`
  Single-project environment model, credentials, and expected variables.
- `docs/data_assets_reference.md`
  DAG, SQL, dataset, and reporting asset catalog.
- `docs/governance_operator_runbook.md`
  How to run the operator workflow end to end.
- `docs/bigquery_apply_order.md`
  Exact SQL apply order and what each group changes.

## Known Limits

- Live BigQuery correctness still depends on your real datasets, permissions, and upstream freshness.
- External API quota behavior is instrumented and bounded in code, but actual Kalshi/Odds provider behavior still needs live observation.
- The backend currently runs under the existing local Python 3.9 environment, which emits upstream deprecation warnings. The repo still works, but upgrading the runtime is recommended.
