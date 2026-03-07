-- BigQuery Standard SQL
-- Kalshi-first baseline tables for market data ingestion and autonomous DE governance.
-- Replace `YOUR_PROJECT_ID` before execution.

CREATE SCHEMA IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_raw`;
CREATE SCHEMA IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_stg`;
CREATE SCHEMA IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_core`;
CREATE SCHEMA IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_ops`;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_raw.api_call_log` (
  call_id STRING NOT NULL,
  endpoint STRING NOT NULL,
  request_url STRING,
  request_params JSON,
  response_status INT64,
  response_headers JSON,
  response_cursor STRING,
  fetched_at TIMESTAMP NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY endpoint;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_raw.events_raw` (
  call_id STRING NOT NULL,
  cursor STRING,
  event_ticker STRING,
  series_ticker STRING,
  title STRING,
  sub_title STRING,
  status STRING,
  last_updated_ts TIMESTAMP,
  api_payload JSON NOT NULL,
  fetched_at TIMESTAMP NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY series_ticker, event_ticker;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_raw.markets_raw` (
  call_id STRING NOT NULL,
  cursor STRING,
  market_ticker STRING,
  event_ticker STRING,
  series_ticker STRING,
  status STRING,
  close_time TIMESTAMP,
  api_payload JSON NOT NULL,
  fetched_at TIMESTAMP NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY series_ticker, status;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_raw.trades_raw` (
  call_id STRING NOT NULL,
  cursor STRING,
  trade_id STRING,
  market_ticker STRING,
  created_time TIMESTAMP,
  api_payload JSON NOT NULL,
  fetched_at TIMESTAMP NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY market_ticker;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_raw.orderbooks_raw` (
  call_id STRING NOT NULL,
  market_ticker STRING,
  api_payload JSON NOT NULL,
  fetched_at TIMESTAMP NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY market_ticker;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_stg.market_snapshots_stg` (
  market_ticker STRING NOT NULL,
  event_ticker STRING,
  series_ticker STRING,
  status STRING,
  yes_bid_dollars NUMERIC,
  yes_ask_dollars NUMERIC,
  no_bid_dollars NUMERIC,
  no_ask_dollars NUMERIC,
  last_price_dollars NUMERIC,
  volume_dollars NUMERIC,
  open_interest_dollars NUMERIC,
  close_time TIMESTAMP,
  snapshot_ts TIMESTAMP NOT NULL,
  source_call_id STRING NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY series_ticker, status;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_stg.events_stg` (
  event_ticker STRING NOT NULL,
  series_ticker STRING,
  title STRING,
  sub_title STRING,
  status STRING,
  last_updated_ts TIMESTAMP,
  snapshot_ts TIMESTAMP NOT NULL,
  source_call_id STRING NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY series_ticker, event_ticker;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_stg.trades_stg` (
  trade_id STRING NOT NULL,
  market_ticker STRING NOT NULL,
  yes_price_dollars NUMERIC,
  no_price_dollars NUMERIC,
  count_contracts NUMERIC,
  taker_side STRING,
  created_time TIMESTAMP NOT NULL,
  snapshot_ts TIMESTAMP NOT NULL,
  source_call_id STRING NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY market_ticker;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_stg.orderbook_levels_stg` (
  market_ticker STRING NOT NULL,
  side STRING NOT NULL,
  price_dollars NUMERIC,
  quantity_contracts NUMERIC,
  level_rank INT64,
  snapshot_ts TIMESTAMP NOT NULL,
  source_call_id STRING NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY market_ticker, side;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_core.events_dim` (
  event_ticker STRING NOT NULL,
  series_ticker STRING,
  title STRING,
  sub_title STRING,
  status STRING,
  last_updated_ts TIMESTAMP,
  last_seen_snapshot_ts TIMESTAMP,
  is_latest BOOL NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY series_ticker, event_ticker;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_core.market_state_core` (
  market_ticker STRING NOT NULL,
  event_ticker STRING,
  series_ticker STRING,
  status STRING,
  yes_bid_dollars NUMERIC,
  yes_ask_dollars NUMERIC,
  no_bid_dollars NUMERIC,
  no_ask_dollars NUMERIC,
  last_price_dollars NUMERIC,
  volume_dollars NUMERIC,
  open_interest_dollars NUMERIC,
  close_time TIMESTAMP,
  snapshot_ts TIMESTAMP NOT NULL,
  is_latest BOOL NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY series_ticker, market_ticker;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_core.trade_prints_core` (
  trade_id STRING NOT NULL,
  market_ticker STRING NOT NULL,
  yes_price_dollars NUMERIC,
  no_price_dollars NUMERIC,
  count_contracts NUMERIC,
  taker_side STRING,
  created_time TIMESTAMP NOT NULL,
  snapshot_ts TIMESTAMP NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY market_ticker;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_core.kpi_5m` (
  kpi_ts TIMESTAMP NOT NULL,
  series_ticker STRING,
  open_market_count INT64,
  total_volume_dollars NUMERIC,
  total_open_interest_dollars NUMERIC,
  trade_count_lookback INT64,
  updated_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(kpi_ts)
CLUSTER BY series_ticker;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_core.dashboard_events` (
  event_ts TIMESTAMP NOT NULL,
  user_id STRING,
  dashboard_id STRING,
  action STRING NOT NULL,
  panel_id STRING,
  filter_json JSON,
  session_id STRING,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY dashboard_id, action;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_ops.rate_limit_log` (
  run_id STRING NOT NULL,
  decided_at TIMESTAMP NOT NULL,
  usage_tier STRING,
  configured_read_rps INT64 NOT NULL,
  effective_read_rps INT64 NOT NULL,
  read_budget_per_run INT64 NOT NULL,
  max_market_pages INT64 NOT NULL,
  max_trade_pages INT64 NOT NULL,
  max_orderbooks INT64 NOT NULL,
  decision_reason STRING NOT NULL
)
PARTITION BY DATE(decided_at)
CLUSTER BY effective_read_rps;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_ops.quality_results` (
  run_id STRING NOT NULL,
  check_name STRING NOT NULL,
  status STRING NOT NULL,
  metric_value FLOAT64,
  threshold_value FLOAT64,
  details JSON,
  checked_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(checked_at)
CLUSTER BY status, check_name;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_ops.schema_change_proposals` (
  proposal_id STRING NOT NULL,
  proposed_at TIMESTAMP NOT NULL,
  proposed_by STRING NOT NULL,
  target_dataset STRING NOT NULL,
  target_table STRING NOT NULL,
  change_type STRING NOT NULL,
  change_sql STRING NOT NULL,
  risk_level STRING NOT NULL,
  rationale STRING,
  source_doc_hash STRING,
  status STRING NOT NULL
)
PARTITION BY DATE(proposed_at)
CLUSTER BY target_dataset, target_table, status;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_ops.schema_change_decisions` (
  proposal_id STRING NOT NULL,
  decided_at TIMESTAMP NOT NULL,
  decision STRING NOT NULL,
  decision_reason STRING,
  decided_by STRING NOT NULL,
  applied_job_id STRING
)
PARTITION BY DATE(decided_at)
CLUSTER BY decision;
