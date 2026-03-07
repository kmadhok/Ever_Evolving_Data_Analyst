-- BigQuery Standard SQL
-- Adds first-class event ingestion tables for the Kalshi pipeline.
-- Project: brainrot-453319

CREATE TABLE IF NOT EXISTS `brainrot-453319.kalshi_raw.events_raw` (
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

CREATE TABLE IF NOT EXISTS `brainrot-453319.kalshi_stg.events_stg` (
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

CREATE TABLE IF NOT EXISTS `brainrot-453319.kalshi_core.events_dim` (
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
