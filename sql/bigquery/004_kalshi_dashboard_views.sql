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

CREATE OR REPLACE VIEW `brainrot-453319.kalshi_dash.vw_simple_side_summary_24h` AS
WITH latest_market_map AS (
  SELECT
    market_ticker, event_ticker
  FROM `brainrot-453319.kalshi_core.market_state_core`
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY market_ticker
    ORDER BY snapshot_ts DESC, ingestion_date DESC, close_time DESC, event_ticker DESC
  ) = 1
),
events_latest AS (
  SELECT event_ticker, title, sub_title
  FROM `brainrot-453319.kalshi_core.events_dim`
),
latest_trade AS (
  SELECT
    market_ticker,
    created_time AS last_trade_ts,
    COALESCE(yes_price_dollars, 0) AS latest_contract_price
  FROM `brainrot-453319.kalshi_core.trade_prints_core`
  QUALIFY ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY created_time DESC) = 1
),
trade_24h AS (
  SELECT
    market_ticker,
    COUNT(*) AS trades_24h,
    SUM(COALESCE(count_contracts, 0)) AS contracts_24h,
    AVG(COALESCE(yes_price_dollars, 0)) AS avg_contract_price_24h,
    SUM(COALESCE(count_contracts, 0) * COALESCE(yes_price_dollars, 0)) AS notional_traded_24h
  FROM `brainrot-453319.kalshi_core.trade_prints_core`
  WHERE created_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
  GROUP BY market_ticker
),
base AS (
  SELECT
    t.market_ticker,
    COALESCE(m.event_ticker, REGEXP_REPLACE(t.market_ticker, r'-[^-]+$', '')) AS event_key,
    COALESCE(e.title, COALESCE(m.event_ticker, REGEXP_REPLACE(t.market_ticker, r'-[^-]+$', ''))) AS event_title,
    e.sub_title AS event_sub_title,
    REGEXP_EXTRACT(t.market_ticker, r'([^-]+)$') AS side_label,
    lt.latest_contract_price,
    lt.last_trade_ts,
    t.trades_24h,
    t.contracts_24h,
    t.avg_contract_price_24h,
    t.notional_traded_24h
  FROM trade_24h AS t
  LEFT JOIN latest_market_map AS m USING (market_ticker)
  LEFT JOIN latest_trade AS lt USING (market_ticker)
  LEFT JOIN events_latest AS e
    ON e.event_ticker = COALESCE(m.event_ticker, REGEXP_REPLACE(t.market_ticker, r'-[^-]+$', ''))
  WHERE NOT STARTS_WITH(COALESCE(m.event_ticker, REGEXP_REPLACE(t.market_ticker, r'-[^-]+$', '')), 'KXMVECROSSCATEGORY-')
    AND NOT STARTS_WITH(COALESCE(m.event_ticker, REGEXP_REPLACE(t.market_ticker, r'-[^-]+$', '')), 'KXMVESPORTSMULTIGAMEEXTENDED-')
    AND NOT REGEXP_CONTAINS(COALESCE(m.event_ticker, REGEXP_REPLACE(t.market_ticker, r'-[^-]+$', '')), r'(SPREAD|TOTAL|PTS|MOV)')
),
scoped AS (
  SELECT
    *,
    COUNT(*) OVER (PARTITION BY event_key) AS side_count,
    DENSE_RANK() OVER (PARTITION BY event_key ORDER BY latest_contract_price DESC, contracts_24h DESC) AS price_rank
  FROM base
)
SELECT
  event_title,
  event_sub_title,
  side_label,
  CONCAT(event_title, ' - ', side_label) AS simple_market_label,
  market_ticker,
  trades_24h,
  contracts_24h,
  ROUND(avg_contract_price_24h, 4) AS avg_contract_price_24h,
  ROUND(notional_traded_24h, 2) AS notional_traded_24h,
  ROUND(latest_contract_price, 4) AS latest_contract_price,
  ROUND(1 - latest_contract_price, 4) AS latest_no_price,
  CASE
    WHEN price_rank = 1 THEN 'favorite'
    WHEN side_count = 2 THEN 'underdog'
    ELSE 'other'
  END AS favorite_status,
  last_trade_ts
FROM scoped
WHERE side_count BETWEEN 2 AND 4
ORDER BY event_title, latest_contract_price DESC, contracts_24h DESC;

CREATE OR REPLACE VIEW `brainrot-453319.kalshi_dash.vw_simple_side_flow_24h` AS
WITH latest_market_map AS (
  SELECT
    market_ticker, event_ticker
  FROM `brainrot-453319.kalshi_core.market_state_core`
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY market_ticker
    ORDER BY snapshot_ts DESC, ingestion_date DESC, close_time DESC, event_ticker DESC
  ) = 1
),
events_latest AS (
  SELECT event_ticker, title
  FROM `brainrot-453319.kalshi_core.events_dim`
),
trade_flow AS (
  SELECT
    market_ticker,
    COUNT(*) AS trades_24h,
    SUM(CASE WHEN LOWER(COALESCE(taker_side, '')) = 'yes' THEN 1 ELSE 0 END) AS yes_trade_count_24h,
    SUM(CASE WHEN LOWER(COALESCE(taker_side, '')) = 'no' THEN 1 ELSE 0 END) AS no_trade_count_24h,
    SUM(COALESCE(count_contracts, 0)) AS contracts_24h,
    SUM(CASE WHEN LOWER(COALESCE(taker_side, '')) = 'yes' THEN COALESCE(count_contracts, 0) ELSE 0 END)
      AS yes_contracts_24h,
    SUM(CASE WHEN LOWER(COALESCE(taker_side, '')) = 'no' THEN COALESCE(count_contracts, 0) ELSE 0 END)
      AS no_contracts_24h
  FROM `brainrot-453319.kalshi_core.trade_prints_core`
  WHERE created_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
  GROUP BY market_ticker
),
base AS (
  SELECT
    COALESCE(e.title, COALESCE(m.event_ticker, REGEXP_REPLACE(f.market_ticker, r'-[^-]+$', ''))) AS event_title,
    REGEXP_EXTRACT(f.market_ticker, r'([^-]+)$') AS side_label,
    f.market_ticker,
    f.trades_24h,
    f.contracts_24h,
    f.yes_trade_count_24h,
    f.no_trade_count_24h,
    f.yes_contracts_24h,
    f.no_contracts_24h
  FROM trade_flow AS f
  LEFT JOIN latest_market_map AS m USING (market_ticker)
  LEFT JOIN events_latest AS e
    ON e.event_ticker = COALESCE(m.event_ticker, REGEXP_REPLACE(f.market_ticker, r'-[^-]+$', ''))
  WHERE NOT STARTS_WITH(COALESCE(m.event_ticker, REGEXP_REPLACE(f.market_ticker, r'-[^-]+$', '')), 'KXMVECROSSCATEGORY-')
    AND NOT STARTS_WITH(COALESCE(m.event_ticker, REGEXP_REPLACE(f.market_ticker, r'-[^-]+$', '')), 'KXMVESPORTSMULTIGAMEEXTENDED-')
    AND NOT REGEXP_CONTAINS(COALESCE(m.event_ticker, REGEXP_REPLACE(f.market_ticker, r'-[^-]+$', '')), r'(SPREAD|TOTAL|PTS|MOV)')
),
scoped AS (
  SELECT
    *,
    COUNT(*) OVER (PARTITION BY event_title) AS side_count
  FROM base
)
SELECT
  event_title,
  side_label,
  CONCAT(event_title, ' - ', side_label) AS simple_market_label,
  market_ticker,
  trades_24h,
  contracts_24h,
  yes_trade_count_24h,
  no_trade_count_24h,
  yes_contracts_24h,
  no_contracts_24h,
  CASE
    WHEN yes_contracts_24h > no_contracts_24h THEN 'more buying YES'
    WHEN no_contracts_24h > yes_contracts_24h THEN 'more selling / NO'
    ELSE 'balanced'
  END AS simple_flow_read
FROM scoped
WHERE side_count BETWEEN 2 AND 4
ORDER BY event_title, contracts_24h DESC;

CREATE OR REPLACE VIEW `brainrot-453319.kalshi_dash.vw_simple_side_price_trend_24h` AS
WITH latest_market_map AS (
  SELECT
    market_ticker, event_ticker
  FROM `brainrot-453319.kalshi_core.market_state_core`
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY market_ticker
    ORDER BY snapshot_ts DESC, ingestion_date DESC, close_time DESC, event_ticker DESC
  ) = 1
),
events_latest AS (
  SELECT event_ticker, title
  FROM `brainrot-453319.kalshi_core.events_dim`
),
hourly AS (
  SELECT
    t.market_ticker,
    TIMESTAMP_TRUNC(t.created_time, HOUR) AS hour_ts,
    ARRAY_AGG(COALESCE(t.yes_price_dollars, 0) ORDER BY t.created_time DESC LIMIT 1)[OFFSET(0)]
      AS last_contract_price,
    COUNT(*) AS trades_in_hour,
    SUM(COALESCE(t.count_contracts, 0)) AS contracts_in_hour
  FROM `brainrot-453319.kalshi_core.trade_prints_core` AS t
  WHERE t.created_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
  GROUP BY t.market_ticker, hour_ts
),
event_sides AS (
  SELECT
    COALESCE(m.event_ticker, REGEXP_REPLACE(h.market_ticker, r'-[^-]+$', '')) AS event_key,
    COUNT(DISTINCT h.market_ticker) AS side_count
  FROM hourly AS h
  LEFT JOIN latest_market_map AS m USING (market_ticker)
  GROUP BY event_key
),
base AS (
  SELECT
    h.hour_ts,
    COALESCE(e.title, COALESCE(m.event_ticker, REGEXP_REPLACE(h.market_ticker, r'-[^-]+$', ''))) AS event_title,
    REGEXP_EXTRACT(h.market_ticker, r'([^-]+)$') AS side_label,
    CONCAT(
      COALESCE(e.title, COALESCE(m.event_ticker, REGEXP_REPLACE(h.market_ticker, r'-[^-]+$', ''))),
      ' - ',
      REGEXP_EXTRACT(h.market_ticker, r'([^-]+)$')
    ) AS simple_market_label,
    h.market_ticker,
    h.last_contract_price,
    h.trades_in_hour,
    h.contracts_in_hour,
    s.side_count
  FROM hourly AS h
  LEFT JOIN latest_market_map AS m USING (market_ticker)
  LEFT JOIN event_sides AS s
    ON s.event_key = COALESCE(m.event_ticker, REGEXP_REPLACE(h.market_ticker, r'-[^-]+$', ''))
  LEFT JOIN events_latest AS e
    ON e.event_ticker = COALESCE(m.event_ticker, REGEXP_REPLACE(h.market_ticker, r'-[^-]+$', ''))
  WHERE NOT STARTS_WITH(COALESCE(m.event_ticker, REGEXP_REPLACE(h.market_ticker, r'-[^-]+$', '')), 'KXMVECROSSCATEGORY-')
    AND NOT STARTS_WITH(COALESCE(m.event_ticker, REGEXP_REPLACE(h.market_ticker, r'-[^-]+$', '')), 'KXMVESPORTSMULTIGAMEEXTENDED-')
    AND NOT REGEXP_CONTAINS(COALESCE(m.event_ticker, REGEXP_REPLACE(h.market_ticker, r'-[^-]+$', '')), r'(SPREAD|TOTAL|PTS|MOV)')
)
SELECT
  hour_ts,
  event_title,
  side_label,
  simple_market_label,
  market_ticker,
  ROUND(last_contract_price, 4) AS last_contract_price,
  trades_in_hour,
  contracts_in_hour
FROM base
WHERE side_count BETWEEN 2 AND 4
ORDER BY hour_ts DESC, event_title, last_contract_price DESC;

CREATE OR REPLACE VIEW `brainrot-453319.kalshi_dash.vw_consumer_matchup_view` AS
WITH latest_market_map AS (
  SELECT
    market_ticker,
    event_ticker,
    status,
    close_time,
    last_price_dollars
  FROM `brainrot-453319.kalshi_core.market_state_core`
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY market_ticker
    ORDER BY snapshot_ts DESC, ingestion_date DESC, close_time DESC, event_ticker DESC
  ) = 1
),
events_latest AS (
  SELECT
    event_ticker,
    title,
    sub_title,
    status
  FROM `brainrot-453319.kalshi_core.events_dim`
),
trade_stats AS (
  SELECT
    market_ticker,
    ARRAY_AGG(COALESCE(yes_price_dollars, 0) ORDER BY created_time ASC LIMIT 1)[OFFSET(0)] AS first_yes_price,
    ARRAY_AGG(COALESCE(yes_price_dollars, 0) ORDER BY created_time DESC LIMIT 1)[OFFSET(0)] AS latest_yes_price,
    MIN(COALESCE(yes_price_dollars, 0)) AS min_yes_price,
    MAX(COALESCE(yes_price_dollars, 0)) AS max_yes_price
  FROM `brainrot-453319.kalshi_core.trade_prints_core`
  GROUP BY market_ticker
),
base AS (
  SELECT
    t.market_ticker,
    COALESCE(m.event_ticker, REGEXP_REPLACE(t.market_ticker, r'-[^-]+$', '')) AS event_key,
    COALESCE(e.title, COALESCE(m.event_ticker, REGEXP_REPLACE(t.market_ticker, r'-[^-]+$', ''))) AS event,
    REGEXP_EXTRACT(t.market_ticker, r'([^-]+)$') AS side,
    COALESCE(t.latest_yes_price, m.last_price_dollars, 0) AS latest_yes_price,
    COALESCE(t.first_yes_price, m.last_price_dollars, 0) AS first_yes_price,
    COALESCE(t.min_yes_price, m.last_price_dollars, 0) AS min_yes_price,
    COALESCE(t.max_yes_price, m.last_price_dollars, 0) AS max_yes_price,
    COALESCE(
      FORMAT_TIMESTAMP('%Y-%m-%d %H:%M UTC', m.close_time),
      e.sub_title,
      'not available'
    ) AS event_time,
    LOWER(COALESCE(m.status, e.status, '')) AS market_status
  FROM trade_stats AS t
  LEFT JOIN latest_market_map AS m USING (market_ticker)
  LEFT JOIN events_latest AS e
    ON e.event_ticker = COALESCE(m.event_ticker, REGEXP_REPLACE(t.market_ticker, r'-[^-]+$', ''))
  WHERE NOT STARTS_WITH(COALESCE(m.event_ticker, REGEXP_REPLACE(t.market_ticker, r'-[^-]+$', '')), 'KXMVECROSSCATEGORY-')
    AND NOT STARTS_WITH(COALESCE(m.event_ticker, REGEXP_REPLACE(t.market_ticker, r'-[^-]+$', '')), 'KXMVESPORTSMULTIGAMEEXTENDED-')
    AND NOT REGEXP_CONTAINS(COALESCE(m.event_ticker, REGEXP_REPLACE(t.market_ticker, r'-[^-]+$', '')), r'(SPREAD|TOTAL|PTS|MOV)')
),
scoped AS (
  SELECT
    *,
    COUNT(*) OVER (PARTITION BY event_key) AS side_count,
    DENSE_RANK() OVER (PARTITION BY event_key ORDER BY latest_yes_price DESC, max_yes_price DESC) AS price_rank,
    MAX(
      IF(
        market_status NOT IN ('open', 'active')
        AND latest_yes_price >= 0.99,
        side,
        NULL
      )
    ) OVER (PARTITION BY event_key) AS resolved_winner
  FROM base
)
SELECT
  event,
  side,
  ROUND(latest_yes_price, 4) AS yes_now,
  ROUND(1 - latest_yes_price, 4) AS no_now,
  ROUND(first_yes_price, 4) AS first_value,
  ROUND(latest_yes_price, 4) AS latest_value,
  ROUND(min_yes_price, 4) AS min_value,
  ROUND(max_yes_price, 4) AS max_value,
  CASE
    WHEN price_rank = 1 THEN 'favorite'
    WHEN side_count = 2 THEN 'underdog'
    ELSE 'other'
  END AS favorite_or_underdog,
  event_time,
  resolved_winner AS result
FROM scoped
WHERE side_count BETWEEN 2 AND 4
ORDER BY event_time ASC, event, latest_value DESC;

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
