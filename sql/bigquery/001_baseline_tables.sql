-- BigQuery Standard SQL
-- Replace `YOUR_PROJECT_ID` before execution.

CREATE SCHEMA IF NOT EXISTS `YOUR_PROJECT_ID.odds_raw`;
CREATE SCHEMA IF NOT EXISTS `YOUR_PROJECT_ID.odds_stg`;
CREATE SCHEMA IF NOT EXISTS `YOUR_PROJECT_ID.odds_core`;
CREATE SCHEMA IF NOT EXISTS `YOUR_PROJECT_ID.odds_ops`;

-- Raw API telemetry and payload capture.
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.odds_raw.api_call_log` (
  call_id STRING NOT NULL,
  endpoint STRING NOT NULL,
  request_url STRING,
  request_params JSON,
  response_status INT64,
  response_headers JSON,
  x_requests_remaining INT64,
  x_requests_used INT64,
  x_requests_last INT64,
  fetched_at TIMESTAMP NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY endpoint;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.odds_raw.odds_events_raw` (
  call_id STRING NOT NULL,
  sport_key STRING NOT NULL,
  market_key STRING,
  region_key STRING,
  event_id STRING,
  commence_time TIMESTAMP,
  api_payload JSON NOT NULL,
  fetched_at TIMESTAMP NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY sport_key, market_key, region_key;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.odds_raw.scores_events_raw` (
  call_id STRING NOT NULL,
  sport_key STRING NOT NULL,
  event_id STRING,
  commence_time TIMESTAMP,
  completed BOOL,
  api_payload JSON NOT NULL,
  fetched_at TIMESTAMP NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY sport_key, completed;

-- Typed staging tables.
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.odds_stg.odds_prices_stg` (
  event_id STRING NOT NULL,
  sport_key STRING NOT NULL,
  commence_time TIMESTAMP,
  bookmaker_key STRING,
  market_key STRING,
  outcome_name STRING,
  odds_price FLOAT64,
  snapshot_ts TIMESTAMP NOT NULL,
  source_call_id STRING NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY sport_key, bookmaker_key, market_key;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.odds_stg.scores_stg` (
  event_id STRING NOT NULL,
  sport_key STRING NOT NULL,
  commence_time TIMESTAMP,
  home_team STRING,
  away_team STRING,
  home_score INT64,
  away_score INT64,
  completed BOOL,
  snapshot_ts TIMESTAMP NOT NULL,
  source_call_id STRING NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY sport_key, completed;

-- Certified core tables.
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.odds_core.odds_prices_core` (
  event_id STRING NOT NULL,
  sport_key STRING NOT NULL,
  commence_time TIMESTAMP,
  bookmaker_key STRING,
  market_key STRING,
  outcome_name STRING,
  odds_price FLOAT64,
  implied_probability FLOAT64,
  snapshot_ts TIMESTAMP NOT NULL,
  is_latest_for_event BOOL NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY sport_key, market_key, event_id;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.odds_core.event_outcomes_core` (
  event_id STRING NOT NULL,
  sport_key STRING NOT NULL,
  commence_time TIMESTAMP,
  home_team STRING,
  away_team STRING,
  home_score INT64,
  away_score INT64,
  winner STRING,
  completed BOOL NOT NULL,
  snapshot_ts TIMESTAMP NOT NULL,
  ingestion_date DATE NOT NULL
)
PARTITION BY ingestion_date
CLUSTER BY sport_key, completed;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.odds_core.kpi_hourly` (
  kpi_ts TIMESTAMP NOT NULL,
  sport_key STRING NOT NULL,
  market_key STRING,
  total_events INT64,
  completed_events INT64,
  avg_implied_prob FLOAT64,
  odds_snapshot_count INT64,
  updated_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(kpi_ts)
CLUSTER BY sport_key, market_key;

-- Dashboard interaction telemetry for feedback loop.
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.odds_core.dashboard_events` (
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

-- Ops + governance tables for autonomous DE actions.
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.odds_ops.quality_results` (
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

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.odds_ops.credit_budget_log` (
  run_id STRING NOT NULL,
  decided_at TIMESTAMP NOT NULL,
  target_sport STRING NOT NULL,
  target_regions STRING NOT NULL,
  target_markets STRING NOT NULL,
  x_requests_remaining INT64,
  x_requests_used INT64,
  estimated_odds_cost INT64 NOT NULL,
  estimated_scores_cost INT64 NOT NULL,
  estimated_cycle_cost INT64 NOT NULL,
  days_remaining_in_month INT64 NOT NULL,
  target_cycles_today INT64 NOT NULL,
  paid_hours_utc STRING NOT NULL,
  include_scores_this_run BOOL NOT NULL,
  should_run_paid BOOL NOT NULL,
  decision_reason STRING NOT NULL
)
PARTITION BY DATE(decided_at)
CLUSTER BY should_run_paid, target_sport;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.odds_ops.schema_change_proposals` (
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

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.odds_ops.schema_change_decisions` (
  proposal_id STRING NOT NULL,
  decided_at TIMESTAMP NOT NULL,
  decision STRING NOT NULL,
  decision_reason STRING,
  decided_by STRING NOT NULL,
  applied_job_id STRING
)
PARTITION BY DATE(decided_at)
CLUSTER BY decision;

CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.odds_ops.run_summary` (
  run_id STRING NOT NULL,
  run_status STRING NOT NULL,
  summary_json JSON,
  emitted_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(emitted_at)
CLUSTER BY run_status;
