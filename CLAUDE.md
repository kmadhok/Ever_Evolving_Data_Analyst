# CLAUDE.md — Project Context for LLM Assistants

## Project Overview

Autonomous Kalshi market data ingestion platform with governance, signals reporting, and multi-role dashboards. Ingests data from Kalshi (REST + WebSocket) and Odds API into BigQuery, with a FastAPI backend and React frontend for dashboards and operator governance.

**GCP Project:** `brainrot-453319`

## Architecture

```
Kalshi API (REST + WebSocket) ──► Airflow DAGs ──► BigQuery (medallion layers)
Odds API ─────────────────────►                    ├─ RAW  (append-only JSON)
                                                   ├─ STG  (typed, append)
                                                   ├─ CORE (deduped, truth)
                                                   ├─ DASH (UI views)
                                                   ├─ SIGNAL (analytics)
                                                   └─ OPS  (observability)
                                                        │
                                         ┌──────────────┼──────────────┐
                                         ▼              ▼              ▼
                                    FastAPI (8000)  React UI (5173)  CLI/Scripts
```

## Directory Structure

```
airflow/dags/                    # Airflow DAGs
  kalshi_market_data_autonomous_de_v0.py  # REST ingestion (5-min, ~83k lines)
  kalshi_ws_realtime_v0.py                # WebSocket ingestion (5-min, ~620 lines)
  odds_api_autonomous_de_v0.py            # Odds API (hourly, ~30k lines)
apps/api/                        # FastAPI backend
apps/ui/                         # React + Vite frontend
sql/bigquery/                    # DDL files (001-011), applied via scripts/apply_bigquery_sql.py
scripts/                         # Helper scripts (apply SQL, trigger DAGs, env checks)
docs/                            # Design docs, plans, data model, asset reference
docker-compose.airflow.yml       # Airflow stack (Postgres + webserver + scheduler)
Makefile                         # All operational commands
```

## Key Commands

```bash
# Airflow
make airflow-up                           # Start Airflow stack
make airflow-down                         # Stop
make airflow-logs                         # Tail logs
make airflow-trigger-kalshi               # Trigger REST DAG
make airflow-trigger-kalshi-ws            # Trigger WebSocket DAG
make airflow-unpause-kalshi-ws            # Unpause WebSocket DAG

# BigQuery SQL
make bq-apply-kalshi-core PROJECT_ID=brainrot-453319
make bq-apply-kalshi-signals PROJECT_ID=brainrot-453319
make bq-apply-kalshi-ws PROJECT_ID=brainrot-453319
make bq-apply-odds-core PROJECT_ID=brainrot-453319

# Backend API
make api-dev                              # FastAPI dev server (port 8000)
make api-test-unit                        # Run tests

# Frontend
make ui-dev                               # Vite dev server (port 5173)
make ui-test                              # Vitest
make ui-build                             # Production build

# Docker Compose (must pass -f)
docker compose -f docker-compose.airflow.yml exec airflow-scheduler <cmd>
docker compose -f docker-compose.airflow.yml logs -f airflow-scheduler
```

## BigQuery Datasets

| Dataset | Purpose |
|---------|---------|
| `kalshi_raw` | Raw API payloads (REST + WS) |
| `kalshi_stg` | Typed/parsed staging tables |
| `kalshi_core` | Deduped business tables (source of truth) |
| `kalshi_dash` | Read-only dashboard views |
| `kalshi_signal` | Analytics/signal views |
| `kalshi_ops` | Operational metadata (quality, connection logs, etc.) |
| `odds_raw` / `odds_stg` / `odds_core` / `odds_ops` | Odds API pipeline |

## WebSocket DAG (`kalshi_ws_realtime_v0`)

**Task flow:** `resolve_top_markets` → `open_ws_session` → `persist_raw` → `build_ws_staging` → `log_ws_connection`

**What it does:**
1. Queries `kalshi_core.market_state_core` for top-N markets by volume
2. Connects to `wss://api.elections.kalshi.com/trade-api/ws/v2` with RSA-PSS auth
3. Subscribes to `ticker`, `trade`, `orderbook_delta` channels
4. Listens for 240 seconds (configurable), collects messages in-memory
5. Inserts raw messages to `kalshi_raw.ws_{trades,ticker,orderbook_snapshots}_raw`
6. Transforms raw → staging: `kalshi_stg.ws_{trades,ticker}_stg`
7. Logs session to `kalshi_ops.ws_connection_log`

**Key env vars:**
```
KALSHI_API_KEY              # API key ID
KALSHI_API_SECRET           # Path to RSA PEM (in container: /opt/airflow/keys/kalshi_private_key.pem)
KALSHI_WS_ENDPOINT          # wss://api.elections.kalshi.com/trade-api/ws/v2
KALSHI_WS_LISTEN_SECONDS    # Default 240
KALSHI_WS_MAX_MARKETS       # Default 25
KALSHI_WS_RECONNECT_DELAY_SECONDS  # Default 2
KALSHI_WS_MAX_RECONNECTS    # Default 3
```

**Auth:** RSA-PSS SHA256 signature over `{timestamp_ms}GET/trade-api/ws/v2`

## REST DAG (`kalshi_market_data_autonomous_de_v0`)

Fetches markets, events, trades, orderbooks every 5 minutes via REST API. Runs through raw → stg → core pipeline with quality gates, signals, and market intelligence reports.

**Key env vars:**
```
KALSHI_API_BASE_URL         # https://api.elections.kalshi.com/trade-api/v2
KALSHI_READ_RPS             # Default 5
KALSHI_MAX_REQUESTS_PER_RUN # Default 120
```

## Docker / Infrastructure

- **Image:** `apache/airflow:2.10.3-python3.11`
- **Executor:** LocalExecutor
- **Compose file:** `docker-compose.airflow.yml`
- **Mounted credentials:**
  - `./credentials_brainrot.json` → `/opt/airflow/keys/credentials.json` (GCP SA)
  - `./kalshi_private_key.pem` → `/opt/airflow/keys/kalshi_private_key.pem` (WS auth)
- **Python deps installed at startup:** `google-cloud-bigquery==3.25.0`, `websocket-client==1.8.0`, `cryptography==44.0.0`

## FastAPI Backend

**Base URL:** `http://localhost:8000`

Key endpoints:
- `GET /v1/dashboard/spec?role=de|analyst|ds|consumer` — Dashboard spec
- `GET /v1/dashboard/tile/{tile_id}` — Tile data (BQ query)
- `GET /v1/signals/feed` — Signal feed
- `POST /v1/governance/run` — Trigger autonomy run
- `GET /v1/governance/summary` — Governance status

## SQL Apply Script

`scripts/apply_bigquery_sql.py` applies SQL files in groups with `YOUR_PROJECT_ID` replacement:
- `kalshi-core`: 002, 004, 007, 008
- `kalshi-signals`: 009, 010
- `kalshi-ws`: 011
- `odds-core`: 001

## Debugging the WS DAG

1. **Check logs:** `docker compose -f docker-compose.airflow.yml logs -f airflow-scheduler`
2. **Test a task:** `docker compose -f docker-compose.airflow.yml exec airflow-scheduler airflow tasks test kalshi_ws_realtime_v0 resolve_top_markets 2026-03-22`
3. **Verify tables exist:** `bq query --project_id=brainrot-453319 --use_legacy_sql=false 'SELECT table_name FROM kalshi_raw.__TABLES__ WHERE table_name LIKE "ws%"'`
4. **Check connection log:** `SELECT * FROM kalshi_ops.ws_connection_log ORDER BY connected_at DESC LIMIT 5`

**Common failures:**
- `resolve_top_markets` fails → `kalshi_core.market_state_core` is empty (REST DAG hasn't run yet)
- WS auth fails → check PEM mount + `KALSHI_API_KEY` + `KALSHI_API_SECRET` env vars
- BQ insert fails → tables not created; run `make bq-apply-kalshi-ws PROJECT_ID=brainrot-453319`

## Documentation

| File | What |
|------|------|
| `docs/plan_kalshi_ws_realtime.md` | WebSocket implementation blueprint |
| `docs/data_model.md` | Medallion architecture + schemas |
| `docs/data_assets_reference.md` | DAG/SQL/dataset catalog |
| `docs/commands_and_scripts.md` | Make targets + script inventory |
| `docs/kalshi_data_guide.md` | Betting concepts + asset inventory |
| `docs/bigquery_apply_order.md` | SQL deployment order |
