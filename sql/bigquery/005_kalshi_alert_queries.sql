-- BigQuery Standard SQL
-- Starter alert checks for autonomous monitoring.
-- Project: brainrot-453319

-- ALERT 1: Stale market snapshot (> 15 minutes).
SELECT
  "stale_market_snapshot" AS alert_name,
  CASE WHEN minutes_since_latest_market_snapshot > 15 THEN "TRIGGER" ELSE "OK" END AS alert_status,
  minutes_since_latest_market_snapshot AS observed_value,
  15 AS threshold_value,
  CURRENT_TIMESTAMP() AS checked_at
FROM `brainrot-453319.kalshi_dash.vw_pipeline_heartbeat`;

-- ALERT 2: Stale trades (> 15 minutes).
SELECT
  "stale_trades" AS alert_name,
  CASE WHEN minutes_since_latest_trade > 15 THEN "TRIGGER" ELSE "OK" END AS alert_status,
  minutes_since_latest_trade AS observed_value,
  15 AS threshold_value,
  CURRENT_TIMESTAMP() AS checked_at
FROM `brainrot-453319.kalshi_dash.vw_pipeline_heartbeat`;

-- ALERT 3: Quality failures in last hour.
SELECT
  "quality_failure_last_60m" AS alert_name,
  CASE WHEN quality_failures_last_60m > 0 THEN "TRIGGER" ELSE "OK" END AS alert_status,
  quality_failures_last_60m AS observed_value,
  0 AS threshold_value,
  CURRENT_TIMESTAMP() AS checked_at
FROM `brainrot-453319.kalshi_dash.vw_pipeline_heartbeat`;

-- ALERT 4: API call volume drop (< 10 calls in last hour).
SELECT
  "api_calls_drop_last_60m" AS alert_name,
  CASE WHEN api_calls_last_60m < 10 THEN "TRIGGER" ELSE "OK" END AS alert_status,
  api_calls_last_60m AS observed_value,
  10 AS threshold_value,
  CURRENT_TIMESTAMP() AS checked_at
FROM `brainrot-453319.kalshi_dash.vw_pipeline_heartbeat`;

-- ALERT 5: Throughput collapse (last 15m vs prior 15m).
WITH windows AS (
  SELECT
    SUM(CASE WHEN fetched_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 15 MINUTE) THEN 1 ELSE 0 END) AS calls_last_15m,
    SUM(
      CASE
        WHEN fetched_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 MINUTE)
         AND fetched_at < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 15 MINUTE)
        THEN 1
        ELSE 0
      END
    ) AS calls_prev_15m
  FROM `brainrot-453319.kalshi_raw.api_call_log`
)
SELECT
  "throughput_collapse_15m" AS alert_name,
  CASE
    WHEN calls_prev_15m = 0 THEN "OK"
    WHEN SAFE_DIVIDE(calls_last_15m, calls_prev_15m) < 0.5 THEN "TRIGGER"
    ELSE "OK"
  END AS alert_status,
  SAFE_DIVIDE(calls_last_15m, calls_prev_15m) AS observed_value,
  0.5 AS threshold_value,
  CURRENT_TIMESTAMP() AS checked_at
FROM windows;

-- ALERT 6: Stale signal run (> 15 minutes).
SELECT
  "stale_signal_run" AS alert_name,
  CASE
    WHEN minutes_since_latest_signal_run > 15 THEN "TRIGGER"
    ELSE "OK"
  END AS alert_status,
  minutes_since_latest_signal_run AS observed_value,
  15 AS threshold_value,
  CURRENT_TIMESTAMP() AS checked_at
FROM (
  SELECT
    IFNULL(TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(run_ts), MINUTE), 1e9) AS minutes_since_latest_signal_run
  FROM `brainrot-453319.kalshi_ops.signal_runs`
);
