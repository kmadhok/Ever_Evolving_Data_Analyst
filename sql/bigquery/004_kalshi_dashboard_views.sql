-- BigQuery Standard SQL
-- Materialized dashboard layer via logical views for Looker Studio / BI tools.
-- Project: brainrot-453319

CREATE SCHEMA IF NOT EXISTS `brainrot-453319.kalshi_dash`;

CREATE OR REPLACE VIEW `brainrot-453319.kalshi_dash.vw_pipeline_heartbeat` AS
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

CREATE OR REPLACE VIEW `brainrot-453319.kalshi_dash.vw_quality_checks_24h` AS
SELECT
  run_id,
  check_name,
  status,
  metric_value,
  threshold_value,
  checked_at
FROM `brainrot-453319.kalshi_ops.quality_results`
WHERE checked_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR);

CREATE OR REPLACE VIEW `brainrot-453319.kalshi_dash.vw_ingestion_throughput_24h` AS
SELECT
  TIMESTAMP_TRUNC(fetched_at, MINUTE) AS ts_minute,
  endpoint,
  COUNT(*) AS api_call_count
FROM `brainrot-453319.kalshi_raw.api_call_log`
WHERE fetched_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
GROUP BY ts_minute, endpoint;

CREATE OR REPLACE VIEW `brainrot-453319.kalshi_dash.vw_kpi_5m_24h` AS
SELECT
  kpi_ts,
  COALESCE(series_ticker, "ALL") AS series_ticker,
  open_market_count,
  total_volume_dollars,
  total_open_interest_dollars,
  trade_count_lookback
FROM `brainrot-453319.kalshi_core.kpi_5m`
WHERE kpi_ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR);

CREATE OR REPLACE VIEW `brainrot-453319.kalshi_dash.vw_top_active_markets_24h` AS
WITH events_latest AS (
  SELECT event_ticker, title, sub_title
  FROM `brainrot-453319.kalshi_core.events_dim`
  WHERE is_latest = TRUE
)
SELECT
  market_ticker,
  m.event_ticker,
  COALESCE(e.title, m.event_ticker) AS event_title,
  e.sub_title AS event_sub_title,
  status,
  last_price_dollars,
  volume_dollars,
  open_interest_dollars,
  snapshot_ts
FROM `brainrot-453319.kalshi_core.market_state_core` AS m
LEFT JOIN events_latest AS e USING (event_ticker)
WHERE snapshot_ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
  AND LOWER(status) = "active";

CREATE OR REPLACE VIEW `brainrot-453319.kalshi_dash.vw_trade_flow_6h` AS
SELECT
  TIMESTAMP_TRUNC(created_time, MINUTE) AS ts_minute,
  COALESCE(LOWER(taker_side), "unknown") AS taker_side,
  COUNT(*) AS trade_count,
  SUM(COALESCE(count_contracts, 0)) AS contracts_traded,
  AVG(COALESCE(yes_price_dollars, 0)) AS avg_yes_price_dollars
FROM `brainrot-453319.kalshi_core.trade_prints_core`
WHERE created_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 6 HOUR)
GROUP BY ts_minute, taker_side;

CREATE OR REPLACE VIEW `brainrot-453319.kalshi_dash.vw_most_traded_markets_60m` AS
WITH latest_market AS (
  SELECT market_ticker, event_ticker
  FROM `brainrot-453319.kalshi_core.market_state_core`
  WHERE is_latest = TRUE
),
events_latest AS (
  SELECT event_ticker, title, sub_title
  FROM `brainrot-453319.kalshi_core.events_dim`
  WHERE is_latest = TRUE
),
trade_enriched AS (
  SELECT
    t.*,
    COALESCE(m.event_ticker, REGEXP_EXTRACT(t.market_ticker, r'^(.*)-[^-]+$')) AS event_ticker
  FROM `brainrot-453319.kalshi_core.trade_prints_core` AS t
  LEFT JOIN latest_market AS m USING (market_ticker)
  WHERE t.created_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 60 MINUTE)
)
SELECT
  t.market_ticker,
  t.event_ticker,
  COALESCE(e.title, t.event_ticker) AS event_title,
  e.sub_title AS event_sub_title,
  COUNT(*) AS trade_count,
  SUM(COALESCE(t.count_contracts, 0)) AS contracts_traded,
  AVG(COALESCE(t.yes_price_dollars, 0)) AS avg_yes_price_dollars,
  MIN(t.created_time) AS first_trade_ts,
  MAX(t.created_time) AS last_trade_ts
FROM trade_enriched AS t
LEFT JOIN events_latest AS e USING (event_ticker)
GROUP BY t.market_ticker, t.event_ticker, event_title, event_sub_title;

CREATE OR REPLACE VIEW `brainrot-453319.kalshi_dash.vw_price_movers_60m` AS
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
),
latest_market AS (
  SELECT market_ticker, event_ticker
  FROM `brainrot-453319.kalshi_core.market_state_core`
  WHERE is_latest = TRUE
),
events_latest AS (
  SELECT event_ticker, title, sub_title
  FROM `brainrot-453319.kalshi_core.events_dim`
  WHERE is_latest = TRUE
),
first_enriched AS (
  SELECT
    f.*,
    COALESCE(m.event_ticker, REGEXP_EXTRACT(f.market_ticker, r'^(.*)-[^-]+$')) AS event_ticker
  FROM first_last AS f
  LEFT JOIN latest_market AS m USING (market_ticker)
)
SELECT
  f.market_ticker,
  f.event_ticker,
  COALESCE(e.title, f.event_ticker) AS event_title,
  e.sub_title AS event_sub_title,
  f.first_yes_price,
  f.last_yes_price,
  (f.last_yes_price - f.first_yes_price) AS abs_move,
  SAFE_DIVIDE(f.last_yes_price - f.first_yes_price, NULLIF(f.first_yes_price, 0)) AS pct_move,
  f.first_ts,
  f.last_ts
FROM first_enriched AS f
LEFT JOIN events_latest AS e USING (event_ticker)
WHERE first_yes_price IS NOT NULL
  AND last_yes_price IS NOT NULL;

CREATE OR REPLACE VIEW `brainrot-453319.kalshi_dash.vw_event_activity_24h` AS
WITH latest_market AS (
  SELECT market_ticker, event_ticker
  FROM `brainrot-453319.kalshi_core.market_state_core`
  WHERE is_latest = TRUE
),
events_latest AS (
  SELECT event_ticker, title, sub_title
  FROM `brainrot-453319.kalshi_core.events_dim`
  WHERE is_latest = TRUE
),
trade_enriched AS (
  SELECT
    t.*,
    COALESCE(m.event_ticker, REGEXP_EXTRACT(t.market_ticker, r'^(.*)-[^-]+$')) AS event_ticker
  FROM `brainrot-453319.kalshi_core.trade_prints_core` AS t
  LEFT JOIN latest_market AS m USING (market_ticker)
  WHERE t.created_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
)
SELECT
  t.event_ticker,
  COALESCE(e.title, t.event_ticker) AS event_title,
  e.sub_title AS event_sub_title,
  COUNT(DISTINCT t.market_ticker) AS market_count_24h,
  COUNT(*) AS trade_count_24h,
  SUM(COALESCE(t.count_contracts, 0)) AS contracts_traded_24h,
  AVG(COALESCE(t.yes_price_dollars, 0)) AS avg_yes_price_dollars_24h,
  MAX(t.created_time) AS last_trade_ts
FROM trade_enriched AS t
LEFT JOIN events_latest AS e USING (event_ticker)
GROUP BY t.event_ticker, event_title, event_sub_title;

CREATE OR REPLACE VIEW `brainrot-453319.kalshi_dash.vw_orderbook_top_snapshot` AS
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
  MAX(IF(side = "yes", price_dollars, NULL)) AS best_yes_price,
  MAX(IF(side = "no", price_dollars, NULL)) AS best_no_price,
  MAX(IF(side = "yes", quantity_contracts, NULL)) AS top_yes_qty,
  MAX(IF(side = "no", quantity_contracts, NULL)) AS top_no_qty,
  MAX(snapshot_ts) AS snapshot_ts
FROM book
GROUP BY market_ticker;

CREATE OR REPLACE VIEW `brainrot-453319.kalshi_dash.vw_endpoint_gap_24h` AS
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
GROUP BY endpoint, ts_minute;
