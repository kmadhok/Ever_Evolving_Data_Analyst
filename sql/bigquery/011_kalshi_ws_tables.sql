-- BigQuery Standard SQL
-- WebSocket real-time ingestion tables.
-- Replace `YOUR_PROJECT_ID` before execution.

-- ---------------------------------------------------------------------------
-- OPS: connection-level session log
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_ops.ws_connection_log` (
  run_id STRING NOT NULL,
  connected_at TIMESTAMP NOT NULL,
  disconnected_at TIMESTAMP,
  listen_duration_seconds INT64,
  markets_subscribed INT64,
  channels STRING,
  messages_received INT64,
  trade_messages INT64,
  ticker_messages INT64,
  orderbook_messages INT64,
  reconnect_count INT64,
  error_message STRING,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY channels;

-- ---------------------------------------------------------------------------
-- RAW: WebSocket trade messages
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_raw.ws_trades_raw` (
  run_id STRING NOT NULL,
  sid INT64,
  trade_id STRING,
  market_ticker STRING,
  yes_price_dollars STRING,
  no_price_dollars STRING,
  count_fp STRING,
  taker_side STRING,
  ts INT64,
  api_payload STRING,
  received_at TIMESTAMP NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY market_ticker;

-- ---------------------------------------------------------------------------
-- RAW: WebSocket ticker (price / volume / OI) messages
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_raw.ws_ticker_raw` (
  run_id STRING NOT NULL,
  sid INT64,
  market_ticker STRING,
  price_dollars STRING,
  yes_bid_dollars STRING,
  yes_ask_dollars STRING,
  volume_fp STRING,
  open_interest_fp STRING,
  ts INT64,
  api_payload STRING,
  received_at TIMESTAMP NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY market_ticker;

-- ---------------------------------------------------------------------------
-- RAW: WebSocket orderbook snapshots and deltas
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_raw.ws_orderbook_snapshots_raw` (
  run_id STRING NOT NULL,
  sid INT64,
  seq INT64,
  market_ticker STRING,
  snapshot_type STRING,
  api_payload STRING,
  received_at TIMESTAMP NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY market_ticker;

-- ---------------------------------------------------------------------------
-- STG: Typed, deduplicated WebSocket trades
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_stg.ws_trades_stg` (
  trade_id STRING NOT NULL,
  market_ticker STRING,
  yes_price_dollars NUMERIC,
  no_price_dollars NUMERIC,
  count_contracts NUMERIC,
  taker_side STRING,
  created_time TIMESTAMP,
  received_at TIMESTAMP NOT NULL,
  source_run_id STRING NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY market_ticker;

-- ---------------------------------------------------------------------------
-- STG: Typed WebSocket ticker snapshots
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_stg.ws_ticker_stg` (
  market_ticker STRING,
  price_dollars NUMERIC,
  yes_bid_dollars NUMERIC,
  yes_ask_dollars NUMERIC,
  volume_dollars NUMERIC,
  open_interest_dollars NUMERIC,
  snapshot_ts TIMESTAMP,
  received_at TIMESTAMP NOT NULL,
  source_run_id STRING NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY market_ticker;
