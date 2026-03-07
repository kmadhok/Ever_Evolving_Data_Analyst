# Ever Evolving Software - Market Data v0 Scaffold

This repo contains starter scaffolds for autonomous market-data analytics:

- `docs/odds_autonomy_v0_prd.md`: Odds API free-tier optimized PRD.
- `docs/kalshi_autonomy_v0_prd.md`: Kalshi-first PRD (rate-limit aware).
- `sql/bigquery/001_baseline_tables.sql`: baseline BigQuery schemas/tables.
- `sql/bigquery/002_kalshi_baseline_tables.sql`: Kalshi-first baseline BigQuery schemas/tables.
- `sql/bigquery/003_kalshi_dashboard_tiles.sql`: starter dashboard tile queries for pipeline health, markets, trades, and orderbook views.
- `sql/bigquery/004_kalshi_dashboard_views.sql`: creates reusable BigQuery views in `kalshi_dash` for BI dashboards.
- `sql/bigquery/005_kalshi_alert_queries.sql`: starter alert query templates for freshness, quality, and throughput.
- `sql/bigquery/006_kalshi_autonomy_app_tables.sql`: app autonomy tables for dashboard spec versions and agent proposals.
- `sql/bigquery/007_kalshi_ds_views.sql`: DS-focused BigQuery views for drift, coverage, and retrain-priority signals.
- `sql/bigquery/008_kalshi_event_ingestion_tables.sql`: raw/staging/core event-entity tables for human-readable market context.
- `docs/kalshi_dashboard_v0_looker_plan.md`: Looker Studio build plan with chart-to-view mapping.
- `docs/kalshi_autonomous_app_architecture.md`: FastAPI + React autonomy architecture and data flow.
- `apps/api`: FastAPI backend serving spec-driven dashboard and agent-proposal endpoints.
- `apps/ui`: React frontend that renders tiles dynamically from API spec.
- `airflow/dags/odds_api_autonomous_de_v0.py`: Airflow DAG skeleton with credit-aware branch control and bounded DE autonomy.
- `airflow/dags/kalshi_market_data_autonomous_de_v0.py`: Airflow DAG skeleton with rate-limit aware branch control and bounded DE autonomy.
- `docker-compose.airflow.yml`: easiest local Airflow stack (webserver + scheduler + postgres).
- `.env.airflow.local.example`: local env template for Airflow Docker.
- `Makefile`: helper commands (`airflow-init`, `airflow-up`, `airflow-down`, `airflow-logs`).

## Recommended Primary DAG
- Use `kalshi_market_data_autonomous_de_v0` as primary.
- Keep `odds_api_autonomous_de_v0` as optional companion source on lower cadence.

## Easiest Airflow Setup (Local Docker)
1. Create your local env file:
   - `cp .env.airflow.local.example .env.airflow.local`
2. Confirm these values in `.env.airflow.local`:
   - `BQ_PROJECT=brainrot-453319`
   - `GOOGLE_APPLICATION_CREDENTIALS_HOST=./credentials_brainrot.json`
3. Initialize Airflow DB and admin user:
   - `make airflow-init`
4. Start scheduler + webserver:
   - `make airflow-up`
5. Open Airflow UI:
   - `http://localhost:8080` (default user/password: `airflow`/`airflow`)
6. In the UI, unpause and trigger:
   - `kalshi_market_data_autonomous_de_v0`

## Next Setup Steps
1. Replace `YOUR_PROJECT_ID` in both SQL files.
2. Apply DDL in BigQuery.
3. Configure Airflow environment variables for the DAG you run.

BigQuery format note:
   - `BQ_PROJECT` must be project id only (example: `brainrot-453319`), not `project.dataset`.

Odds DAG variables:
   - `ODDS_API_KEY`
   - `TARGET_SPORT` (example: `basketball_nba`)
   - `TARGET_REGIONS` (example: `us`)
   - `TARGET_MARKETS` (example: `h2h`)
   - `BQ_PROJECT`
   - `MONTHLY_CREDIT_CAP` (default `500`)
   - `CREDIT_RESERVE` (default `25`)
   - `MAX_PAID_CYCLES_PER_DAY` (default `8`)
   - `ENABLE_SCORES_FETCH` (default `true`)

Kalshi DAG variables:
   - `KALSHI_API_BASE_URL` (default `https://api.elections.kalshi.com/trade-api/v2`)
   - `KALSHI_USAGE_TIER` (default `basic`)
   - `KALSHI_READ_RPS` (default `5`)
   - `KALSHI_MAX_TIER_READ_RPS` (default `20`)
   - `KALSHI_MAX_REQUESTS_PER_RUN` (default `120`)
   - `KALSHI_MAX_EVENT_PAGES` (default `60`, used as targeted event-detail call budget)
   - `KALSHI_EVENT_BACKFILL_MAX_EVENTS` (default `20`, max events hydrated per run from recent trade demand)
   - `KALSHI_EVENT_BACKFILL_LOOKBACK_HOURS` (default `24`, candidate window for hydration)
   - `KALSHI_MARKET_STATUS` (default `open`)
   - `KALSHI_SERIES_TICKER` (optional filter)
   - `KALSHI_429_RETRY_DELAY_SECONDS` (default `2`)
   - `QUALITY_MAX_FRESHNESS_MINUTES` (default `15`)
   - `QUALITY_MAX_NULL_MARKET_TICKER_RATIO` (default `0.01`)
   - `QUALITY_MAX_DUPLICATE_TRADE_ID_RATIO` (default `0.01`)
   - `BQ_PROJECT`
   - `GOOGLE_APPLICATION_CREDENTIALS` (path to service-account json for local runs)

4. Run the Kalshi DAG; remaining TODOs are non-essential v0 items (agent schema proposals and external alert sink).

## Config Placement
- Local/dev: `.env` is fine and commonly easiest.
- Airflow/prod: use Airflow Variables/Connections and secret manager (not checked-in `.env` files).
- Starter local template: `.env.kalshi.local.example`.
- Airflow Docker local template: `.env.airflow.local.example`.

## Free Tier Behavior
- DAG runs hourly but does a no-cost quota heartbeat first (`/sports/`).
- Paid pulls are executed only in computed UTC paid slots.
- When credits are tight, policy downgrades to odds-only (skips scores).

## Kalshi Behavior
- DAG runs every 5 minutes and enforces conservative read RPS.
- Uses cursor pagination for markets/trades and capped orderbook pulls.
- Fetches targeted event metadata using `/events/{event_ticker}` for ingested markets.
- Runs event-title hydration backfill from high-volume recent trade events.
- Uses dollar/fixed-point fields in staging/core (cent fields removed in recent API versions).

## v0 Safety Model
- Only additive low-risk schema changes may auto-apply.
- Core table breaking changes remain blocked.
- Quality gates run before any autonomous schema action.

## Autonomous App Stack (FastAPI + React)
This stack removes manual BI-layout dependency and enables spec-driven evolution.

1. Apply app autonomy tables:
   - `sql/bigquery/006_kalshi_autonomy_app_tables.sql`
2. Start API:
   - `cd /Users/kanumadhok/Downloads/code/Ever_Evolving_Software/apps/api`
   - `python3 -m venv .venv && source .venv/bin/activate`
   - `pip install -r requirements.txt`
   - `cp .env.example .env`
   - `export GOOGLE_APPLICATION_CREDENTIALS=/Users/kanumadhok/Downloads/code/Ever_Evolving_Software/credentials_brainrot.json`
   - `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`
3. Start UI:
   - `cd /Users/kanumadhok/Downloads/code/Ever_Evolving_Software/apps/ui`
   - `npm install`
   - `VITE_API_BASE_URL=http://localhost:8000 npm run dev`
   - Open role views:
     - `http://localhost:5173/de`
     - `http://localhost:5173/analyst`
     - `http://localhost:5173/ds`

Autonomy loop path:
- UI usage events -> `kalshi_core.dashboard_events`
- Agent proposals -> `kalshi_ops.agent_proposals`
- Active dashboard spec -> `kalshi_ops.dashboard_spec_versions`
- API always serves latest active spec (fallback default if missing)
- Role routes -> `/de`, `/analyst`, `/ds` (role-filtered spec from API)
