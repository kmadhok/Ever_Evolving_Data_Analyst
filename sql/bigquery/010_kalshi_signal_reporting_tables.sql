-- BigQuery Standard SQL
-- Reporting tables for autonomous Kalshi market-intelligence runs.
-- Replace `YOUR_PROJECT_ID` before execution.

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_ops.signal_runs` (
  run_id STRING NOT NULL,
  run_ts TIMESTAMP NOT NULL,
  status STRING NOT NULL,
  signal_count INT64,
  signal_type_counts JSON,
  top_entities_json JSON,
  top_signals_json JSON,
  created_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(run_ts)
CLUSTER BY status;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_ops.market_intelligence_reports` (
  report_id STRING NOT NULL,
  report_ts TIMESTAMP NOT NULL,
  report_type STRING NOT NULL,
  summary_json JSON,
  top_signals_json JSON,
  top_markets_json JSON,
  top_events_json JSON,
  created_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(report_ts)
CLUSTER BY report_type;
