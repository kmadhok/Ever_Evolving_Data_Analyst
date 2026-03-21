-- BigQuery Standard SQL
-- WebSocket real-time ingestion observability tables.
-- Replace `YOUR_PROJECT_ID` before execution.

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_ops.ws_connection_log` (
  run_id STRING NOT NULL,
  connected_at TIMESTAMP NOT NULL,
  disconnected_at TIMESTAMP,
  listen_duration_seconds INT64,
  markets_subscribed INT64,
  channels STRING,
  messages_received INT64,
  trade_messages INT64,
  orderbook_messages INT64,
  reconnect_count INT64,
  error_message STRING,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY channels;
