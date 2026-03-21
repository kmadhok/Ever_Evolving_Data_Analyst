# Plan: Kalshi WebSocket Real-Time Ingestion

## Overview

Add a new Airflow DAG (`kalshi_ws_realtime_v0.py`) that connects to Kalshi's
authenticated WebSocket API, subscribes to **ticker**, **trade**, and
**orderbook_delta** channels for the top-N markets, streams messages for a
configurable window (default 4 min), then lands + stages the data in BigQuery.
Runs every 5 minutes — overlapping with the existing REST DAG to give us
**near-real-time trade and price data** (~seconds latency) alongside the
existing 5-minute batch polling.

---

## Step 1 — BigQuery Tables (SQL DDL)

**File:** `sql/bigquery/011_kalshi_ws_tables.sql` (extend existing)

Add three new tables alongside the existing `ws_connection_log`:

### 1a. `kalshi_raw.ws_trades_raw`
| Column | Type | Notes |
|--------|------|-------|
| run_id | STRING | WS session identifier |
| sid | INT64 | Subscription ID from WS |
| trade_id | STRING | From msg.trade_id |
| market_ticker | STRING | From msg.market_ticker |
| yes_price_dollars | STRING | Raw string from WS |
| no_price_dollars | STRING | Raw string from WS |
| count_fp | STRING | Raw string (float-point contracts) |
| taker_side | STRING | "yes" or "no" |
| ts | INT64 | Unix epoch from msg.ts |
| api_payload | STRING | Full JSON message |
| received_at | TIMESTAMP | Python `datetime.utcnow()` when msg arrived |
| ingestion_date | DATE | Partition key |

Partition: `ingestion_date` · Cluster: `market_ticker`

### 1b. `kalshi_raw.ws_orderbook_snapshots_raw`
| Column | Type | Notes |
|--------|------|-------|
| run_id | STRING | WS session identifier |
| sid | INT64 | Subscription ID |
| seq | INT64 | Sequence number |
| market_ticker | STRING | |
| snapshot_type | STRING | "snapshot" or "delta" |
| api_payload | STRING | Full JSON message |
| received_at | TIMESTAMP | |
| ingestion_date | DATE | |

Partition: `ingestion_date` · Cluster: `market_ticker`

### 1c. `kalshi_raw.ws_ticker_raw`
| Column | Type | Notes |
|--------|------|-------|
| run_id | STRING | WS session identifier |
| sid | INT64 | Subscription ID |
| market_ticker | STRING | |
| price_dollars | STRING | Last price |
| yes_bid_dollars | STRING | |
| yes_ask_dollars | STRING | |
| volume_fp | STRING | |
| open_interest_fp | STRING | |
| ts | INT64 | Unix epoch |
| api_payload | STRING | Full JSON |
| received_at | TIMESTAMP | |
| ingestion_date | DATE | |

Partition: `ingestion_date` · Cluster: `market_ticker`

---

## Step 2 — Staging SQL (in-DAG, same pattern as REST DAG)

### 2a. `kalshi_stg.ws_trades_stg`
INSERT from `ws_trades_raw` for the current run:
- SAFE_CAST price/count fields to NUMERIC
- Parse `ts` → TIMESTAMP via `TIMESTAMP_SECONDS`
- Deduplicate by `trade_id` (keep earliest `received_at`)

### 2b. `kalshi_stg.ws_ticker_stg`
INSERT from `ws_ticker_raw`:
- SAFE_CAST price/volume/OI to NUMERIC
- Parse `ts` → TIMESTAMP

No staging for orderbook deltas in v0 — raw capture is sufficient for
initial analytics. We can add orderbook reconstruction in a later iteration.

---

## Step 3 — DAG Implementation

**File:** `airflow/dags/kalshi_ws_realtime_v0.py`

### DAG Flow
```
start
  → resolve_top_markets        (query kalshi_core.market_state_core for top-N by volume)
  → open_ws_session             (connect, authenticate, subscribe, listen, collect)
  → persist_raw                 (insert trades/ticker/orderbook rows to BQ raw)
  → build_ws_staging            (run staging SQL for trades + ticker)
  → log_ws_connection           (write to ws_connection_log)
  → end
```

### 3a. `resolve_top_markets` task
- Query `kalshi_core.market_state_core` for top N open markets by
  `volume_dollars DESC` (N = `KALSHI_WS_MAX_MARKETS`, default 25)
- Return list of market_tickers
- Fallback: if core table is empty, use `kalshi_stg.market_snapshots_stg`

### 3b. `open_ws_session` task (the core logic)
- **Auth:** Load `KALSHI_API_KEY` (key ID) and `KALSHI_API_SECRET` (PEM private
  key path or inline). Generate RSA-PSS SHA256 signature over
  `timestamp_ms + "GET" + "/trade-api/ws/v2"`. Set headers:
  - `KALSHI-ACCESS-KEY`
  - `KALSHI-ACCESS-TIMESTAMP`
  - `KALSHI-ACCESS-SIGNATURE`
- **Connect:** `websocket.WebSocketApp` or `websocket.create_connection` from
  `websocket-client` library. Include auth headers in handshake.
- **Subscribe:** Send JSON commands for channels `["ticker", "trade"]` with
  `market_tickers` list. Send separate subscription for `["orderbook_delta"]`
  for the same tickers.
- **Listen loop:** Collect messages into three in-memory lists
  (`trades`, `tickers`, `orderbook_updates`) for `KALSHI_WS_LISTEN_SECONDS`
  (default 240s = 4 min). Tag each message with `received_at` and `run_id`.
- **Reconnect:** On disconnect, retry up to `KALSHI_WS_MAX_RECONNECTS` with
  `KALSHI_WS_RECONNECT_DELAY_SECONDS` backoff.
- **Heartbeat:** `websocket-client` handles ping/pong automatically. Kalshi
  sends ping every 10s.
- **Return:** Dict with trade_rows, ticker_rows, orderbook_rows, and session
  metadata (connected_at, disconnected_at, message counts, errors).

### 3c. `persist_raw` task
- Use `_insert_rows` pattern (same as REST DAG) to insert into:
  - `kalshi_raw.ws_trades_raw`
  - `kalshi_raw.ws_ticker_raw`
  - `kalshi_raw.ws_orderbook_snapshots_raw`
- Batch insert (streaming inserts via `insert_rows_json`)

### 3d. `build_ws_staging` task
- Run parameterized SQL to INSERT from raw → staging for the current `run_id`
- Same SAFE_CAST + COALESCE patterns as REST DAG

### 3e. `log_ws_connection` task
- Insert one row into `kalshi_ops.ws_connection_log` with session summary

### DAG Config
```python
schedule = "*/5 * * * *"   # Every 5 minutes (same as REST)
max_active_runs = 1         # No overlap
catchup = False
start_date = datetime(2026, 1, 1)
```

---

## Step 4 — Auth Helper

**Within the DAG file** (no separate module needed):

```python
def _build_ws_auth_headers() -> dict:
    """Build Kalshi WebSocket auth headers using RSA-PSS."""
    import base64, time
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    api_key = os.environ["KALSHI_API_KEY"]
    secret_path = os.environ["KALSHI_API_SECRET"]

    # Load PEM private key
    with open(secret_path, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)

    timestamp_ms = str(int(time.time() * 1000))
    message = timestamp_ms + "GET" + "/trade-api/ws/v2"

    signature = private_key.sign(
        message.encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                     salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )

    return {
        "KALSHI-ACCESS-KEY": api_key,
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
    }
```

---

## Step 5 — Docker / Infra Updates

**File:** `docker-compose.airflow.yml`

Add `cryptography` to pip requirements (needed for RSA-PSS signing):
```
_PIP_ADDITIONAL_REQUIREMENTS: google-cloud-bigquery==3.25.0 websocket-client==1.8.0 cryptography==44.0.0
```

**File:** `.env.kalshi.local.example`

Already has the needed vars. No changes required.

---

## Step 6 — Env Var for WS Endpoint Fix

The scaffolded endpoint `wss://trading-api.kalshi.com/trade-api/ws/v2` may
not be the correct production endpoint. Per Kalshi docs, the correct endpoint
appears to be `wss://api.elections.kalshi.com/trade-api/ws/v2` (same host as
REST API). Update the default in both `.env.kalshi.local.example` and
`docker-compose.airflow.yml`.

---

## Files Changed (Summary)

| File | Action |
|------|--------|
| `sql/bigquery/011_kalshi_ws_tables.sql` | Extend — add 3 raw tables |
| `airflow/dags/kalshi_ws_realtime_v0.py` | **New** — WebSocket DAG (~400-500 lines) |
| `docker-compose.airflow.yml` | Edit — add `cryptography` pip dep |
| `.env.kalshi.local.example` | Edit — fix WS endpoint default |

---

## What This Does NOT Include (Future Iterations)

- **Orderbook reconstruction** from deltas (v0 just captures raw deltas)
- **REST ↔ WS trade deduplication** in core layer (needs reconciliation logic)
- **Live push to dashboard** (SSE/WebSocket from FastAPI → React)
- **Pub/Sub intermediate** (not needed at current scale)
- **Signal generation** from WS data (reuse existing signal views once
  ws_trades_stg feeds into trade_prints_core)

---

## Execution Order

1. Extend `011_kalshi_ws_tables.sql` with 3 new tables
2. Update `docker-compose.airflow.yml` (add cryptography dep, fix WS endpoint)
3. Update `.env.kalshi.local.example` (fix WS endpoint)
4. Write `kalshi_ws_realtime_v0.py` DAG
5. Commit and push
