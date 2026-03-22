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

## WebSocket DAG (`kalshi_ws_realtime_v0`) — Deep Dive

### Why This Exists

The REST DAG polls Kalshi every 5 minutes, which means trades and price movements between polls are only seen after the fact. The WebSocket DAG complements this by opening a persistent connection to Kalshi's streaming API, capturing **sub-second trade and price data** as it happens. This gives us:

- **Real-time trade capture** — every individual trade with exact timestamp, price, size, and side
- **Live price/volume/OI snapshots** — ticker updates pushed by the exchange whenever state changes
- **Orderbook deltas** — bid/ask level changes as they happen (raw capture only in v0)
- **Overlap with REST** — both DAGs run on 5-min schedules; REST gives us the full market catalog and historical backfill, WS gives us the granular intra-interval activity

### How It Works (Task-by-Task)

**File:** `airflow/dags/kalshi_ws_realtime_v0.py` (~620 lines)
**Schedule:** `*/5 * * * *` (every 5 minutes), `max_active_runs=1`, `catchup=False`

```
start → resolve_top_markets → open_ws_session → persist_raw → build_ws_staging → log_ws_connection → end
```

#### Task 1: `resolve_top_markets`
Determines which markets to subscribe to. Queries `kalshi_core.market_state_core` for the top-N open markets ranked by `volume_dollars DESC` (N defaults to 25 via `KALSHI_WS_MAX_MARKETS`). Falls back to `kalshi_stg.market_snapshots_stg` if the core table is empty or errors. **Fails hard if no markets are found** — this means the REST DAG hasn't run yet.

#### Task 2: `open_ws_session` (the core logic)
This is the main task — it opens an authenticated WebSocket connection, subscribes to channels, and streams messages for a fixed window.

1. **Auth** — Loads RSA private key from `KALSHI_API_SECRET` (PEM file path). Signs `{timestamp_ms}GET/trade-api/ws/v2` with RSA-PSS SHA256. Sends three headers: `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP`, `KALSHI-ACCESS-SIGNATURE`.

2. **Connect** — Uses `websocket-client` library (`websocket.create_connection`) to open a connection to `wss://api.elections.kalshi.com/trade-api/ws/v2` with auth headers in the handshake.

3. **Subscribe** — Sends two subscription commands:
   - `channels: ["ticker", "trade"]` with the resolved market tickers
   - `channels: ["orderbook_delta"]` with the same tickers

4. **Listen** — Collects messages into three in-memory lists (trades, tickers, orderbooks) for `KALSHI_WS_LISTEN_SECONDS` (default 240s = 4 minutes). Each message gets tagged with `received_at` timestamp and the session's `run_id` (UUID). Uses a 15-second recv timeout with retry so it can check the remaining time window.

5. **Reconnect** — On disconnect/error, retries up to `KALSHI_WS_MAX_RECONNECTS` (default 3) with `KALSHI_WS_RECONNECT_DELAY_SECONDS * attempt` backoff. Re-subscribes on each reconnect.

6. **Returns** — A dict with `trades`, `tickers`, `orderbooks` lists and `session_meta` (timestamps, message counts, reconnect count, errors).

**Message routing logic:**
| WS `type` field | Routed to | Key fields extracted |
|-----------------|-----------|---------------------|
| `trade` | trades list | trade_id, market_ticker, yes/no_price_dollars, count_fp, taker_side, ts |
| `ticker` | tickers list | market_ticker, price_dollars, yes_bid/ask_dollars, volume_fp, open_interest_fp, ts |
| `orderbook_delta` / `orderbook_snapshot` | orderbooks list | market_ticker, seq, snapshot_type (delta vs snapshot), full payload |
| anything else (`subscribed`, `ok`, `error`, heartbeat) | silently ignored | — |

#### Task 3: `persist_raw`
Inserts the collected messages into BigQuery raw tables via streaming inserts (`insert_rows_json`):
- `kalshi_raw.ws_trades_raw` — one row per trade message
- `kalshi_raw.ws_ticker_raw` — one row per ticker update
- `kalshi_raw.ws_orderbook_snapshots_raw` — one row per orderbook delta/snapshot

All raw tables store prices as STRING (raw from API) and keep the full `api_payload` JSON.

#### Task 4: `build_ws_staging`
Runs SQL to transform raw → staging for the current `run_id`:
- **`kalshi_stg.ws_trades_stg`** — SAFE_CASTs price/count to NUMERIC, parses `ts` (unix epoch) → TIMESTAMP, deduplicates by `trade_id` (keeps earliest `received_at` via `QUALIFY ROW_NUMBER()`)
- **`kalshi_stg.ws_ticker_stg`** — SAFE_CASTs all numeric fields, parses timestamp
- No staging for orderbook deltas in v0 (raw capture only)

#### Task 5: `log_ws_connection`
Writes one summary row to `kalshi_ops.ws_connection_log` with: run_id, connected/disconnected timestamps, listen duration, markets subscribed, channels, message counts by type, reconnect count, and any error message.

### BigQuery Tables (DDL in `sql/bigquery/011_kalshi_ws_tables.sql`)

**RAW layer** (append-only, STRING prices, full JSON payload):

| Table | Partition | Cluster | Key columns |
|-------|-----------|---------|-------------|
| `kalshi_raw.ws_trades_raw` | `ingestion_date` | `market_ticker` | run_id, trade_id, market_ticker, yes/no_price_dollars, count_fp, taker_side, ts, api_payload, received_at |
| `kalshi_raw.ws_ticker_raw` | `ingestion_date` | `market_ticker` | run_id, market_ticker, price_dollars, yes/no_bid/ask_dollars, volume_fp, open_interest_fp, ts, api_payload, received_at |
| `kalshi_raw.ws_orderbook_snapshots_raw` | `ingestion_date` | `market_ticker` | run_id, seq, market_ticker, snapshot_type, api_payload, received_at |

**STG layer** (typed NUMERIC prices, deduplicated trades):

| Table | Partition | Cluster | Key columns |
|-------|-----------|---------|-------------|
| `kalshi_stg.ws_trades_stg` | `ingestion_date` | `market_ticker` | trade_id, market_ticker, yes/no_price_dollars (NUMERIC), count_contracts (NUMERIC), taker_side, created_time (TIMESTAMP), source_run_id |
| `kalshi_stg.ws_ticker_stg` | `ingestion_date` | `market_ticker` | market_ticker, price_dollars, yes_bid/ask_dollars, volume_dollars, open_interest_dollars (all NUMERIC), snapshot_ts (TIMESTAMP), source_run_id |

**OPS layer:**

| Table | Partition | Cluster | Purpose |
|-------|-----------|---------|---------|
| `kalshi_ops.ws_connection_log` | `ingestion_date` | `channels` | One row per DAG run — session summary with timing, message counts, errors |

### Key Env Vars

```
KALSHI_API_KEY              # API key ID (required)
KALSHI_API_SECRET           # Path to RSA PEM file (in container: /opt/airflow/keys/kalshi_private_key.pem)
KALSHI_WS_ENDPOINT          # Default: wss://api.elections.kalshi.com/trade-api/ws/v2
KALSHI_WS_LISTEN_SECONDS    # Default 240 (4 min listen window within 5-min schedule)
KALSHI_WS_MAX_MARKETS       # Default 25 (top N markets by volume to subscribe)
KALSHI_WS_RECONNECT_DELAY_SECONDS  # Default 2 (multiplied by attempt number)
KALSHI_WS_MAX_RECONNECTS    # Default 3
```

### Auth Details

RSA-PSS SHA256 signature. The signed message is `{timestamp_ms}GET/trade-api/ws/v2` (concatenated, no separators). The PEM private key is loaded from the path in `KALSHI_API_SECRET`. The signature is base64-encoded and sent in the `KALSHI-ACCESS-SIGNATURE` header during the WebSocket handshake.

### Relationship to REST DAG

| Aspect | REST DAG | WebSocket DAG |
|--------|----------|---------------|
| Schedule | Every 5 min | Every 5 min |
| Data freshness | Up to 5 min stale | Sub-second |
| Coverage | All markets (paginated) | Top 25 by volume |
| Trade capture | Batch via `/trades` endpoint | Individual as they happen |
| Orderbook | Full snapshot via `/orderbook` | Deltas as they change |
| Price data | Snapshot at poll time | Every ticker update |
| Pipeline depth | raw → stg → core → dash → signal | raw → stg (core merge TBD) |

### What v0 Does NOT Include (Future Work)

- **Orderbook reconstruction** from deltas (v0 captures raw deltas only)
- **REST + WS trade deduplication** in the core layer (needs reconciliation logic)
- **Core layer merge** for WS data (ws_trades_stg → trade_prints_core)
- **Live push to dashboard** (SSE/WebSocket from FastAPI → React)
- **Signal generation** from WS data (reuse existing signal views once WS feeds core)

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
