-- BigQuery Standard SQL
-- Starter dashboard queries for Kalshi autonomous pipeline.
-- Project is set to: brainrot-453319
-- If you move projects, replace `brainrot-453319` in this file.

-- ============================================================================
-- TILE 01: Pipeline heartbeat (single-row KPI card set)
-- ============================================================================
SELECT
  (
    SELECT TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(snapshot_ts), MINUTE)
    FROM `brainrot-453319.kalshi_core.market_state_core`
  ) AS minutes_since_latest_market_snapshot,
  (
    SELECT TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(created_time), MINUTE)
    FROM `brainrot-453319.kalshi_core.trade_prints_core`
  ) AS minutes_since_latest_trade,
  (
    SELECT TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(snapshot_ts), MINUTE)
    FROM `brainrot-453319.kalshi_stg.orderbook_levels_stg`
  ) AS minutes_since_latest_orderbook_level,
  (
    SELECT COUNT(*)
    FROM `brainrot-453319.kalshi_ops.quality_results`
    WHERE status = 'FAIL'
      AND checked_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 60 MINUTE)
  ) AS quality_failures_last_60m,
  (
    SELECT COUNT(*)
    FROM `brainrot-453319.kalshi_raw.api_call_log`
    WHERE fetched_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 60 MINUTE)
  ) AS api_calls_last_60m;

-- ============================================================================
-- TILE 02: Quality checks timeline (table)
-- ============================================================================
SELECT
  run_id,
  check_name,
  status,
  metric_value,
  threshold_value,
  checked_at
FROM `brainrot-453319.kalshi_ops.quality_results`
WHERE checked_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
ORDER BY checked_at DESC;

-- ============================================================================
-- TILE 03: Raw ingestion throughput by endpoint (time series)
-- ============================================================================
SELECT
  TIMESTAMP_TRUNC(fetched_at, MINUTE) AS ts_minute,
  endpoint,
  COUNT(*) AS api_call_count
FROM `brainrot-453319.kalshi_raw.api_call_log`
WHERE fetched_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
GROUP BY ts_minute, endpoint
ORDER BY ts_minute DESC, endpoint;

-- ============================================================================
-- TILE 04: KPI trend from core.kpi_5m (time series)
-- ============================================================================
SELECT
  kpi_ts,
  COALESCE(series_ticker, 'ALL') AS series_ticker,
  open_market_count,
  total_volume_dollars,
  total_open_interest_dollars,
  trade_count_lookback
FROM `brainrot-453319.kalshi_core.kpi_5m`
WHERE kpi_ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
ORDER BY kpi_ts DESC;

-- ============================================================================
-- TILE 05: Top active markets by volume (table)
-- ============================================================================
SELECT
  market_ticker,
  status,
  last_price_dollars,
  volume_dollars,
  open_interest_dollars,
  snapshot_ts
FROM `brainrot-453319.kalshi_core.market_state_core`
WHERE snapshot_ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
  AND LOWER(status) = 'active'
ORDER BY volume_dollars DESC NULLS LAST
LIMIT 25;

-- ============================================================================
-- TILE 06: Trade flow by side (stacked time series)
-- ============================================================================
SELECT
  TIMESTAMP_TRUNC(created_time, MINUTE) AS ts_minute,
  COALESCE(LOWER(taker_side), 'unknown') AS taker_side,
  COUNT(*) AS trade_count,
  SUM(COALESCE(count_contracts, 0)) AS contracts_traded,
  AVG(COALESCE(yes_price_dollars, 0)) AS avg_yes_price_dollars
FROM `brainrot-453319.kalshi_core.trade_prints_core`
WHERE created_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 6 HOUR)
GROUP BY ts_minute, taker_side
ORDER BY ts_minute DESC, taker_side;

-- ============================================================================
-- TILE 07: Most-traded markets in last 60m (table)
-- ============================================================================
SELECT
  market_ticker,
  COUNT(*) AS trade_count,
  SUM(COALESCE(count_contracts, 0)) AS contracts_traded,
  AVG(COALESCE(yes_price_dollars, 0)) AS avg_yes_price_dollars,
  MIN(created_time) AS first_trade_ts,
  MAX(created_time) AS last_trade_ts
FROM `brainrot-453319.kalshi_core.trade_prints_core`
WHERE created_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 60 MINUTE)
GROUP BY market_ticker
ORDER BY contracts_traded DESC, trade_count DESC
LIMIT 20;

-- ============================================================================
-- TILE 08: Price movers in last 60m (table)
-- ============================================================================
WITH ranked_trades AS (
  SELECT
    market_ticker,
    created_time,
    COALESCE(yes_price_dollars, 0) AS yes_price_dollars,
    ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY created_time ASC) AS rn_first,
    ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY created_time DESC) AS rn_last
  FROM `brainrot-453319.kalshi_core.trade_prints_core`
  WHERE created_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 60 MINUTE)
),
first_last AS (
  SELECT
    market_ticker,
    MAX(IF(rn_first = 1, yes_price_dollars, NULL)) AS first_yes_price,
    MAX(IF(rn_last = 1, yes_price_dollars, NULL)) AS last_yes_price,
    MAX(IF(rn_first = 1, created_time, NULL)) AS first_ts,
    MAX(IF(rn_last = 1, created_time, NULL)) AS last_ts
  FROM ranked_trades
  GROUP BY market_ticker
)
SELECT
  market_ticker,
  first_yes_price,
  last_yes_price,
  (last_yes_price - first_yes_price) AS abs_move,
  SAFE_DIVIDE(last_yes_price - first_yes_price, NULLIF(first_yes_price, 0)) AS pct_move,
  first_ts,
  last_ts
FROM first_last
WHERE first_yes_price IS NOT NULL
  AND last_yes_price IS NOT NULL
ORDER BY ABS(abs_move) DESC
LIMIT 20;

-- ============================================================================
-- TILE 09: Top-of-book snapshot (table)
-- Note: this uses latest orderbook snapshot per market.
-- ============================================================================
WITH latest_market_snap AS (
  SELECT
    market_ticker,
    MAX(snapshot_ts) AS latest_snapshot_ts
  FROM `brainrot-453319.kalshi_stg.orderbook_levels_stg`
  GROUP BY market_ticker
),
book AS (
  SELECT
    l.market_ticker,
    o.side,
    o.price_dollars,
    o.quantity_contracts,
    o.level_rank,
    o.snapshot_ts
  FROM latest_market_snap AS l
  JOIN `brainrot-453319.kalshi_stg.orderbook_levels_stg` AS o
    ON o.market_ticker = l.market_ticker
   AND o.snapshot_ts = l.latest_snapshot_ts
)
SELECT
  market_ticker,
  MAX(IF(side = 'yes', price_dollars, NULL)) AS best_yes_price,
  MAX(IF(side = 'no', price_dollars, NULL)) AS best_no_price,
  MAX(IF(side = 'yes', quantity_contracts, NULL)) AS top_yes_qty,
  MAX(IF(side = 'no', quantity_contracts, NULL)) AS top_no_qty,
  MAX(snapshot_ts) AS snapshot_ts
FROM book
GROUP BY market_ticker
ORDER BY snapshot_ts DESC
LIMIT 50;

-- ============================================================================
-- TILE 10: Endpoint latency proxy (time to next API fetch, in seconds)
-- Useful for confirming 5-minute cadence behavior.
-- ============================================================================
WITH calls AS (
  SELECT
    endpoint,
    fetched_at,
    LEAD(fetched_at) OVER (PARTITION BY endpoint ORDER BY fetched_at) AS next_fetched_at
  FROM `brainrot-453319.kalshi_raw.api_call_log`
  WHERE fetched_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
)
SELECT
  endpoint,
  TIMESTAMP_TRUNC(fetched_at, MINUTE) AS ts_minute,
  AVG(TIMESTAMP_DIFF(next_fetched_at, fetched_at, SECOND)) AS avg_gap_seconds,
  COUNT(*) AS samples
FROM calls
WHERE next_fetched_at IS NOT NULL
GROUP BY endpoint, ts_minute
ORDER BY ts_minute DESC, endpoint;
