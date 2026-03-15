-- BigQuery Standard SQL
-- Autonomous market-intelligence signal layer for the Kalshi warehouse.
-- Replace `YOUR_PROJECT_ID` before execution.

CREATE SCHEMA IF NOT EXISTS `YOUR_PROJECT_ID.kalshi_signal`;

CREATE OR REPLACE VIEW `YOUR_PROJECT_ID.kalshi_signal.vw_market_latest` AS
WITH ranked AS (
  SELECT
    m.*,
    ROW_NUMBER() OVER (
      PARTITION BY market_ticker, snapshot_ts
      ORDER BY ingestion_date DESC, close_time DESC, event_ticker DESC, series_ticker DESC
    ) AS dedupe_rn,
    ROW_NUMBER() OVER (
      PARTITION BY market_ticker
      ORDER BY snapshot_ts DESC, ingestion_date DESC, close_time DESC, event_ticker DESC, series_ticker DESC
    ) AS latest_rn
  FROM `YOUR_PROJECT_ID.kalshi_core.market_state_core` AS m
)
SELECT * EXCEPT (dedupe_rn, latest_rn)
FROM ranked
WHERE dedupe_rn = 1
  AND latest_rn = 1;

CREATE OR REPLACE VIEW `YOUR_PROJECT_ID.kalshi_signal.vw_event_latest` AS
WITH ranked AS (
  SELECT
    e.*,
    ROW_NUMBER() OVER (
      PARTITION BY event_ticker
      ORDER BY last_seen_snapshot_ts DESC, updated_at DESC, ingestion_date DESC
    ) AS latest_rn
  FROM `YOUR_PROJECT_ID.kalshi_core.events_dim` AS e
)
SELECT * EXCEPT (latest_rn)
FROM ranked
WHERE latest_rn = 1;

CREATE OR REPLACE VIEW `YOUR_PROJECT_ID.kalshi_signal.vw_signal_probability_shifts_24h` AS
WITH bounds AS (
  SELECT
    TIMESTAMP_SUB(MAX(created_time), INTERVAL 24 HOUR) AS start_ts,
    MAX(created_time) AS end_ts
  FROM `YOUR_PROJECT_ID.kalshi_core.trade_prints_core`
),
ranked AS (
  SELECT
    t.market_ticker,
    t.created_time,
    COALESCE(t.yes_price_dollars, 0) AS yes_price_dollars,
    ROW_NUMBER() OVER (PARTITION BY t.market_ticker ORDER BY t.created_time ASC) AS rn_first,
    ROW_NUMBER() OVER (PARTITION BY t.market_ticker ORDER BY t.created_time DESC) AS rn_last
  FROM `YOUR_PROJECT_ID.kalshi_core.trade_prints_core` AS t
  CROSS JOIN bounds AS b
  WHERE t.created_time BETWEEN b.start_ts AND b.end_ts
),
first_last AS (
  SELECT
    market_ticker,
    MAX(IF(rn_first = 1, yes_price_dollars, NULL)) AS first_yes_price,
    MAX(IF(rn_last = 1, yes_price_dollars, NULL)) AS last_yes_price,
    COUNT(*) AS trades_24h
  FROM ranked
  GROUP BY market_ticker
),
enriched AS (
  SELECT
    "probability_shift_24h" AS signal_type,
    "24h" AS signal_window,
    b.end_ts AS signal_ts,
    f.market_ticker,
    m.event_ticker,
    COALESCE(e.title, m.event_ticker, f.market_ticker) AS event_title,
    COALESCE(e.title, m.event_ticker, f.market_ticker) AS title,
    REGEXP_EXTRACT(f.market_ticker, r'^([^-]+)') AS market_family,
    f.first_yes_price,
    f.last_yes_price,
    f.last_yes_price - f.first_yes_price AS probability_change_24h,
    SAFE_DIVIDE(f.last_yes_price - f.first_yes_price, NULLIF(f.first_yes_price, 0)) AS probability_pct_change_24h,
    f.trades_24h
  FROM first_last AS f
  CROSS JOIN bounds AS b
  LEFT JOIN `YOUR_PROJECT_ID.kalshi_signal.vw_market_latest` AS m USING (market_ticker)
  LEFT JOIN `YOUR_PROJECT_ID.kalshi_signal.vw_event_latest` AS e
    ON e.event_ticker = m.event_ticker
  WHERE f.first_yes_price IS NOT NULL
    AND f.last_yes_price IS NOT NULL
    AND f.trades_24h >= 5
    AND ABS(f.last_yes_price - f.first_yes_price) >= 0.08
)
SELECT
  CONCAT(signal_type, "|", market_ticker) AS signal_id,
  signal_type,
  signal_window,
  signal_ts,
  market_ticker,
  event_ticker,
  event_title,
  title,
  market_family,
  first_yes_price,
  last_yes_price,
  probability_change_24h,
  probability_pct_change_24h,
  trades_24h,
  CAST(ABS(probability_change_24h) AS FLOAT64) AS score,
  CASE
    WHEN ABS(probability_change_24h) >= 0.20 THEN "high"
    WHEN ABS(probability_change_24h) >= 0.12 THEN "medium"
    ELSE "low"
  END AS severity,
  TO_JSON(
    STRUCT(
      first_yes_price,
      last_yes_price,
      probability_change_24h,
      probability_pct_change_24h,
      trades_24h
    )
  ) AS supporting_metrics_json,
  CONCAT(
    "24h YES probability moved by ",
    CAST(ROUND(probability_change_24h * 100, 2) AS STRING),
    " points across ",
    CAST(trades_24h AS STRING),
    " trades."
  ) AS explanation_short
FROM enriched;

CREATE OR REPLACE VIEW `YOUR_PROJECT_ID.kalshi_signal.vw_signal_volume_spikes_6h` AS
WITH latest_ts AS (
  SELECT MAX(created_time) AS max_ts
  FROM `YOUR_PROJECT_ID.kalshi_core.trade_prints_core`
),
per_market AS (
  SELECT
    t.market_ticker,
    SUM(
      CASE
        WHEN t.created_time >= TIMESTAMP_SUB(l.max_ts, INTERVAL 6 HOUR)
        THEN COALESCE(t.count_contracts, 0)
        ELSE 0
      END
    ) AS contracts_6h,
    SUM(
      CASE
        WHEN t.created_time < TIMESTAMP_SUB(l.max_ts, INTERVAL 6 HOUR)
         AND t.created_time >= TIMESTAMP_SUB(l.max_ts, INTERVAL 30 HOUR)
        THEN COALESCE(t.count_contracts, 0)
        ELSE 0
      END
    ) AS contracts_prev_24h,
    COUNTIF(t.created_time >= TIMESTAMP_SUB(l.max_ts, INTERVAL 6 HOUR)) AS trades_6h
  FROM `YOUR_PROJECT_ID.kalshi_core.trade_prints_core` AS t
  CROSS JOIN latest_ts AS l
  GROUP BY t.market_ticker
),
enriched AS (
  SELECT
    "volume_spike_6h" AS signal_type,
    "6h" AS signal_window,
    l.max_ts AS signal_ts,
    p.market_ticker,
    m.event_ticker,
    COALESCE(e.title, m.event_ticker, p.market_ticker) AS event_title,
    COALESCE(e.title, m.event_ticker, p.market_ticker) AS title,
    REGEXP_EXTRACT(p.market_ticker, r'^([^-]+)') AS market_family,
    p.contracts_6h,
    SAFE_DIVIDE(p.contracts_prev_24h, 4) AS baseline_contracts_6h,
    SAFE_DIVIDE(p.contracts_6h, NULLIF(SAFE_DIVIDE(p.contracts_prev_24h, 4), 0)) AS volume_spike_ratio,
    p.trades_6h
  FROM per_market AS p
  CROSS JOIN latest_ts AS l
  LEFT JOIN `YOUR_PROJECT_ID.kalshi_signal.vw_market_latest` AS m USING (market_ticker)
  LEFT JOIN `YOUR_PROJECT_ID.kalshi_signal.vw_event_latest` AS e
    ON e.event_ticker = m.event_ticker
  WHERE p.trades_6h >= 3
    AND p.contracts_6h > 0
    AND SAFE_DIVIDE(p.contracts_6h, NULLIF(SAFE_DIVIDE(p.contracts_prev_24h, 4), 0)) >= 3
)
SELECT
  CONCAT(signal_type, "|", market_ticker) AS signal_id,
  signal_type,
  signal_window,
  signal_ts,
  market_ticker,
  event_ticker,
  event_title,
  title,
  market_family,
  contracts_6h,
  baseline_contracts_6h,
  volume_spike_ratio,
  trades_6h,
  CAST(LOG(1 + volume_spike_ratio) * LEAST(1.0, SAFE_DIVIDE(trades_6h, 10)) AS FLOAT64) AS score,
  CASE
    WHEN volume_spike_ratio >= 10 THEN "high"
    WHEN volume_spike_ratio >= 5 THEN "medium"
    ELSE "low"
  END AS severity,
  TO_JSON(
    STRUCT(
      contracts_6h,
      baseline_contracts_6h,
      volume_spike_ratio,
      trades_6h
    )
  ) AS supporting_metrics_json,
  CONCAT(
    "6h flow is running at ",
    CAST(ROUND(volume_spike_ratio, 2) AS STRING),
    "x the trailing baseline across ",
    CAST(trades_6h AS STRING),
    " trades."
  ) AS explanation_short
FROM enriched;

CREATE OR REPLACE VIEW `YOUR_PROJECT_ID.kalshi_signal.vw_signal_volatility_spikes_1h` AS
WITH latest_ts AS (
  SELECT MAX(created_time) AS max_ts
  FROM `YOUR_PROJECT_ID.kalshi_core.trade_prints_core`
),
per_market AS (
  SELECT
    t.market_ticker,
    STDDEV_POP(
      CASE
        WHEN t.created_time >= TIMESTAMP_SUB(l.max_ts, INTERVAL 1 HOUR)
        THEN COALESCE(t.yes_price_dollars, 0)
      END
    ) AS vol_1h,
    STDDEV_POP(
      CASE
        WHEN t.created_time >= TIMESTAMP_SUB(l.max_ts, INTERVAL 24 HOUR)
        THEN COALESCE(t.yes_price_dollars, 0)
      END
    ) AS vol_24h,
    COUNTIF(t.created_time >= TIMESTAMP_SUB(l.max_ts, INTERVAL 1 HOUR)) AS trades_1h,
    COUNTIF(t.created_time >= TIMESTAMP_SUB(l.max_ts, INTERVAL 24 HOUR)) AS trades_24h
  FROM `YOUR_PROJECT_ID.kalshi_core.trade_prints_core` AS t
  CROSS JOIN latest_ts AS l
  GROUP BY t.market_ticker
),
enriched AS (
  SELECT
    "volatility_spike_1h" AS signal_type,
    "1h" AS signal_window,
    l.max_ts AS signal_ts,
    p.market_ticker,
    m.event_ticker,
    COALESCE(e.title, m.event_ticker, p.market_ticker) AS event_title,
    COALESCE(e.title, m.event_ticker, p.market_ticker) AS title,
    REGEXP_EXTRACT(p.market_ticker, r'^([^-]+)') AS market_family,
    p.vol_1h,
    p.vol_24h,
    SAFE_DIVIDE(p.vol_1h, NULLIF(p.vol_24h, 0)) AS volatility_ratio,
    p.trades_1h,
    p.trades_24h
  FROM per_market AS p
  CROSS JOIN latest_ts AS l
  LEFT JOIN `YOUR_PROJECT_ID.kalshi_signal.vw_market_latest` AS m USING (market_ticker)
  LEFT JOIN `YOUR_PROJECT_ID.kalshi_signal.vw_event_latest` AS e
    ON e.event_ticker = m.event_ticker
  WHERE p.trades_1h >= 5
    AND p.trades_24h >= 20
    AND SAFE_DIVIDE(p.vol_1h, NULLIF(p.vol_24h, 0)) >= 2
)
SELECT
  CONCAT(signal_type, "|", market_ticker) AS signal_id,
  signal_type,
  signal_window,
  signal_ts,
  market_ticker,
  event_ticker,
  event_title,
  title,
  market_family,
  vol_1h,
  vol_24h,
  volatility_ratio,
  trades_1h,
  trades_24h,
  CAST(volatility_ratio * LEAST(1.0, SAFE_DIVIDE(trades_1h, 20)) AS FLOAT64) AS score,
  CASE
    WHEN volatility_ratio >= 4 THEN "high"
    WHEN volatility_ratio >= 2.5 THEN "medium"
    ELSE "low"
  END AS severity,
  TO_JSON(
    STRUCT(
      vol_1h,
      vol_24h,
      volatility_ratio,
      trades_1h,
      trades_24h
    )
  ) AS supporting_metrics_json,
  CONCAT(
    "1h trade-price volatility is ",
    CAST(ROUND(volatility_ratio, 2) AS STRING),
    "x the 24h reference window."
  ) AS explanation_short
FROM enriched;

CREATE OR REPLACE VIEW `YOUR_PROJECT_ID.kalshi_signal.vw_signal_liquidity_deterioration_latest` AS
WITH latest_book AS (
  SELECT
    market_ticker,
    MAX(snapshot_ts) AS snapshot_ts
  FROM `YOUR_PROJECT_ID.kalshi_stg.orderbook_levels_stg`
  GROUP BY market_ticker
),
top_levels AS (
  SELECT
    l.market_ticker,
    MAX(IF(o.side = 'yes' AND o.level_rank = 1, o.price_dollars, NULL)) AS best_yes_bid,
    MAX(IF(o.side = 'no' AND o.level_rank = 1, o.price_dollars, NULL)) AS best_no_bid,
    MAX(IF(o.side = 'yes' AND o.level_rank = 1, o.quantity_contracts, NULL)) AS best_yes_qty,
    MAX(IF(o.side = 'no' AND o.level_rank = 1, o.quantity_contracts, NULL)) AS best_no_qty,
    MAX(o.snapshot_ts) AS signal_ts
  FROM latest_book AS l
  JOIN `YOUR_PROJECT_ID.kalshi_stg.orderbook_levels_stg` AS o
    ON o.market_ticker = l.market_ticker
   AND o.snapshot_ts = l.snapshot_ts
  GROUP BY l.market_ticker
),
enriched AS (
  SELECT
    "liquidity_deterioration_latest" AS signal_type,
    "latest" AS signal_window,
    b.signal_ts,
    b.market_ticker,
    m.event_ticker,
    COALESCE(e.title, m.event_ticker, b.market_ticker) AS event_title,
    COALESCE(e.title, m.event_ticker, b.market_ticker) AS title,
    REGEXP_EXTRACT(b.market_ticker, r'^([^-]+)') AS market_family,
    b.best_yes_bid,
    b.best_no_bid,
    1 - (b.best_yes_bid + b.best_no_bid) AS implied_spread,
    b.best_yes_qty,
    b.best_no_qty,
    LEAST(COALESCE(b.best_yes_qty, 0), COALESCE(b.best_no_qty, 0)) AS thin_book_qty
  FROM top_levels AS b
  LEFT JOIN `YOUR_PROJECT_ID.kalshi_signal.vw_market_latest` AS m USING (market_ticker)
  LEFT JOIN `YOUR_PROJECT_ID.kalshi_signal.vw_event_latest` AS e
    ON e.event_ticker = m.event_ticker
  WHERE b.best_yes_bid IS NOT NULL
    AND b.best_no_bid IS NOT NULL
    AND (
      1 - (b.best_yes_bid + b.best_no_bid) >= 0.10
      OR LEAST(COALESCE(b.best_yes_qty, 0), COALESCE(b.best_no_qty, 0)) <= 5
    )
)
SELECT
  CONCAT(signal_type, "|", market_ticker) AS signal_id,
  signal_type,
  signal_window,
  signal_ts,
  market_ticker,
  event_ticker,
  event_title,
  title,
  market_family,
  best_yes_bid,
  best_no_bid,
  implied_spread,
  best_yes_qty,
  best_no_qty,
  thin_book_qty,
  CAST(implied_spread + SAFE_DIVIDE(5 - LEAST(thin_book_qty, 5), 25) AS FLOAT64) AS score,
  CASE
    WHEN implied_spread >= 0.25 OR thin_book_qty <= 1 THEN "high"
    WHEN implied_spread >= 0.15 OR thin_book_qty <= 3 THEN "medium"
    ELSE "low"
  END AS severity,
  TO_JSON(
    STRUCT(
      best_yes_bid,
      best_no_bid,
      implied_spread,
      best_yes_qty,
      best_no_qty,
      thin_book_qty
    )
  ) AS supporting_metrics_json,
  CONCAT(
    "Top-of-book implied spread is ",
    CAST(ROUND(implied_spread * 100, 2) AS STRING),
    " points with minimum visible depth of ",
    CAST(thin_book_qty AS STRING),
    "."
  ) AS explanation_short
FROM enriched;

CREATE OR REPLACE VIEW `YOUR_PROJECT_ID.kalshi_signal.vw_signal_open_interest_change_12h` AS
WITH latest_market AS (
  SELECT * FROM `YOUR_PROJECT_ID.kalshi_signal.vw_market_latest`
),
latest_ts AS (
  SELECT MAX(snapshot_ts) AS max_ts
  FROM latest_market
),
prev AS (
  SELECT
    market_ticker,
    open_interest_dollars,
    volume_dollars
  FROM (
    SELECT
      m.market_ticker,
      m.open_interest_dollars,
      m.volume_dollars,
      ROW_NUMBER() OVER (
        PARTITION BY m.market_ticker
        ORDER BY m.snapshot_ts DESC, m.ingestion_date DESC
      ) AS rn
    FROM `YOUR_PROJECT_ID.kalshi_core.market_state_core` AS m
    CROSS JOIN latest_ts AS l
    WHERE m.snapshot_ts <= TIMESTAMP_SUB(l.max_ts, INTERVAL 12 HOUR)
  )
  WHERE rn = 1
),
enriched AS (
  SELECT
    "open_interest_change_12h" AS signal_type,
    "12h" AS signal_window,
    l.max_ts AS signal_ts,
    c.market_ticker,
    c.event_ticker,
    COALESCE(e.title, c.event_ticker, c.market_ticker) AS event_title,
    COALESCE(e.title, c.event_ticker, c.market_ticker) AS title,
    REGEXP_EXTRACT(c.market_ticker, r'^([^-]+)') AS market_family,
    p.open_interest_dollars AS open_interest_12h_ago,
    c.open_interest_dollars AS open_interest_now,
    c.open_interest_dollars - p.open_interest_dollars AS open_interest_change_12h,
    c.volume_dollars - p.volume_dollars AS cumulative_volume_change_12h
  FROM latest_market AS c
  CROSS JOIN latest_ts AS l
  JOIN prev AS p USING (market_ticker)
  LEFT JOIN `YOUR_PROJECT_ID.kalshi_signal.vw_event_latest` AS e
    ON e.event_ticker = c.event_ticker
  WHERE ABS(c.open_interest_dollars - p.open_interest_dollars) > 0
     OR ABS(c.volume_dollars - p.volume_dollars) > 0
)
SELECT
  CONCAT(signal_type, "|", market_ticker) AS signal_id,
  signal_type,
  signal_window,
  signal_ts,
  market_ticker,
  event_ticker,
  event_title,
  title,
  market_family,
  open_interest_12h_ago,
  open_interest_now,
  open_interest_change_12h,
  cumulative_volume_change_12h,
  CAST(ABS(open_interest_change_12h) + SAFE_DIVIDE(ABS(cumulative_volume_change_12h), 10) AS FLOAT64) AS score,
  CASE
    WHEN ABS(open_interest_change_12h) >= 500 THEN "high"
    WHEN ABS(open_interest_change_12h) >= 100 THEN "medium"
    ELSE "low"
  END AS severity,
  TO_JSON(
    STRUCT(
      open_interest_12h_ago,
      open_interest_now,
      open_interest_change_12h,
      cumulative_volume_change_12h
    )
  ) AS supporting_metrics_json,
  CONCAT(
    "Open interest changed by ",
    CAST(ROUND(open_interest_change_12h, 2) AS STRING),
    " over the last 12h."
  ) AS explanation_short
FROM enriched;

CREATE OR REPLACE VIEW `YOUR_PROJECT_ID.kalshi_signal.vw_signal_cross_market_inconsistencies` AS
WITH grouped AS (
  SELECT
    m.event_ticker AS event_key,
    COUNT(*) AS market_count,
    SUM(COALESCE(m.last_price_dollars, 0)) AS sum_yes_probabilities,
    MAX(m.last_price_dollars) - MIN(m.last_price_dollars) AS dispersion
  FROM `YOUR_PROJECT_ID.kalshi_signal.vw_market_latest` AS m
  WHERE LOWER(m.status) IN ("open", "active")
    AND m.event_ticker IS NOT NULL
    AND NOT STARTS_WITH(m.event_ticker, "KXMVECROSSCATEGORY-")
    AND NOT STARTS_WITH(m.event_ticker, "KXMVESPORTSMULTIGAMEEXTENDED-")
  GROUP BY m.event_ticker
),
enriched AS (
  SELECT
    "cross_market_inconsistency" AS signal_type,
    "latest" AS signal_window,
    CURRENT_TIMESTAMP() AS signal_ts,
    g.event_key,
    COALESCE(e.title, g.event_key) AS event_title,
    COALESCE(e.title, g.event_key) AS title,
    REGEXP_EXTRACT(g.event_key, r'^([^-]+)') AS market_family,
    g.market_count,
    g.sum_yes_probabilities,
    g.dispersion
  FROM grouped AS g
  LEFT JOIN `YOUR_PROJECT_ID.kalshi_signal.vw_event_latest` AS e
    ON e.event_ticker = g.event_key
  WHERE g.market_count BETWEEN 2 AND 8
    AND g.sum_yes_probabilities > 1.05
)
SELECT
  CONCAT(signal_type, "|", event_key) AS signal_id,
  signal_type,
  signal_window,
  signal_ts,
  event_key,
  event_title,
  title,
  market_family,
  market_count,
  sum_yes_probabilities,
  dispersion,
  CAST((sum_yes_probabilities - 1.0) + SAFE_DIVIDE(dispersion, 2) AS FLOAT64) AS score,
  CASE
    WHEN sum_yes_probabilities >= 1.25 THEN "high"
    WHEN sum_yes_probabilities >= 1.10 THEN "medium"
    ELSE "low"
  END AS severity,
  TO_JSON(
    STRUCT(
      market_count,
      sum_yes_probabilities,
      dispersion
    )
  ) AS supporting_metrics_json,
  CONCAT(
    "Exclusive-family summed YES probabilities total ",
    CAST(ROUND(sum_yes_probabilities, 3) AS STRING),
    "."
  ) AS explanation_short
FROM enriched;

CREATE OR REPLACE VIEW `YOUR_PROJECT_ID.kalshi_signal.vw_signal_event_reactions_3h` AS
WITH latest_ts AS (
  SELECT MAX(created_time) AS max_ts
  FROM `YOUR_PROJECT_ID.kalshi_core.trade_prints_core`
),
trades AS (
  SELECT
    COALESCE(m.event_ticker, REGEXP_EXTRACT(t.market_ticker, r'^(.*)-[^-]+$')) AS event_key,
    t.market_ticker,
    t.created_time,
    COALESCE(t.count_contracts, 0) AS count_contracts,
    COALESCE(t.yes_price_dollars, 0) AS yes_price_dollars
  FROM `YOUR_PROJECT_ID.kalshi_core.trade_prints_core` AS t
  LEFT JOIN `YOUR_PROJECT_ID.kalshi_signal.vw_market_latest` AS m
    ON m.market_ticker = t.market_ticker
),
agg AS (
  SELECT
    t.event_key,
    COUNT(DISTINCT t.market_ticker) AS markets_active,
    SUM(
      CASE
        WHEN t.created_time >= TIMESTAMP_SUB(l.max_ts, INTERVAL 3 HOUR)
        THEN t.count_contracts
        ELSE 0
      END
    ) AS contracts_3h,
    SUM(
      CASE
        WHEN t.created_time < TIMESTAMP_SUB(l.max_ts, INTERVAL 3 HOUR)
         AND t.created_time >= TIMESTAMP_SUB(l.max_ts, INTERVAL 27 HOUR)
        THEN t.count_contracts
        ELSE 0
      END
    ) AS contracts_prev_24h,
    STDDEV_POP(
      CASE
        WHEN t.created_time >= TIMESTAMP_SUB(l.max_ts, INTERVAL 3 HOUR)
        THEN t.yes_price_dollars
      END
    ) AS vol_3h,
    STDDEV_POP(
      CASE
        WHEN t.created_time >= TIMESTAMP_SUB(l.max_ts, INTERVAL 27 HOUR)
        THEN t.yes_price_dollars
      END
    ) AS vol_27h
  FROM trades AS t
  CROSS JOIN latest_ts AS l
  WHERE t.event_key IS NOT NULL
  GROUP BY t.event_key
),
enriched AS (
  SELECT
    "event_reaction_3h" AS signal_type,
    "3h" AS signal_window,
    l.max_ts AS signal_ts,
    a.event_key,
    COALESCE(e.title, a.event_key) AS event_title,
    COALESCE(e.title, a.event_key) AS title,
    REGEXP_EXTRACT(a.event_key, r'^([^-]+)') AS market_family,
    a.markets_active,
    a.contracts_3h,
    SAFE_DIVIDE(a.contracts_prev_24h, 8) AS baseline_contracts_3h,
    SAFE_DIVIDE(a.contracts_3h, NULLIF(SAFE_DIVIDE(a.contracts_prev_24h, 8), 0)) AS event_volume_spike,
    SAFE_DIVIDE(a.vol_3h, NULLIF(a.vol_27h, 0)) AS event_volatility_ratio
  FROM agg AS a
  CROSS JOIN latest_ts AS l
  LEFT JOIN `YOUR_PROJECT_ID.kalshi_signal.vw_event_latest` AS e
    ON e.event_ticker = a.event_key
  WHERE a.contracts_3h > 0
    AND (
      SAFE_DIVIDE(a.contracts_3h, NULLIF(SAFE_DIVIDE(a.contracts_prev_24h, 8), 0)) >= 3
      OR SAFE_DIVIDE(a.vol_3h, NULLIF(a.vol_27h, 0)) >= 2
    )
)
SELECT
  CONCAT(signal_type, "|", event_key) AS signal_id,
  signal_type,
  signal_window,
  signal_ts,
  event_key,
  event_title,
  title,
  market_family,
  markets_active,
  contracts_3h,
  baseline_contracts_3h,
  event_volume_spike,
  event_volatility_ratio,
  CAST(
    LOG(1 + COALESCE(event_volume_spike, 0)) + COALESCE(event_volatility_ratio, 0)
    AS FLOAT64
  ) AS score,
  CASE
    WHEN COALESCE(event_volume_spike, 0) >= 10 OR COALESCE(event_volatility_ratio, 0) >= 3 THEN "high"
    WHEN COALESCE(event_volume_spike, 0) >= 5 OR COALESCE(event_volatility_ratio, 0) >= 2 THEN "medium"
    ELSE "low"
  END AS severity,
  TO_JSON(
    STRUCT(
      markets_active,
      contracts_3h,
      baseline_contracts_3h,
      event_volume_spike,
      event_volatility_ratio
    )
  ) AS supporting_metrics_json,
  CONCAT(
    "Event cluster volume is ",
    CAST(ROUND(COALESCE(event_volume_spike, 0), 2) AS STRING),
    "x baseline with volatility ratio ",
    CAST(ROUND(COALESCE(event_volatility_ratio, 0), 2) AS STRING),
    "."
  ) AS explanation_short
FROM enriched;

CREATE OR REPLACE VIEW `YOUR_PROJECT_ID.kalshi_signal.vw_signal_feed_latest` AS
SELECT
  signal_id,
  signal_type,
  signal_window,
  "market" AS entity_type,
  market_ticker AS entity_id,
  title,
  market_family,
  score,
  severity,
  signal_ts,
  supporting_metrics_json AS metrics_json,
  explanation_short
FROM `YOUR_PROJECT_ID.kalshi_signal.vw_signal_probability_shifts_24h`
UNION ALL
SELECT
  signal_id,
  signal_type,
  signal_window,
  "market" AS entity_type,
  market_ticker AS entity_id,
  title,
  market_family,
  score,
  severity,
  signal_ts,
  supporting_metrics_json AS metrics_json,
  explanation_short
FROM `YOUR_PROJECT_ID.kalshi_signal.vw_signal_volume_spikes_6h`
UNION ALL
SELECT
  signal_id,
  signal_type,
  signal_window,
  "market" AS entity_type,
  market_ticker AS entity_id,
  title,
  market_family,
  score,
  severity,
  signal_ts,
  supporting_metrics_json AS metrics_json,
  explanation_short
FROM `YOUR_PROJECT_ID.kalshi_signal.vw_signal_volatility_spikes_1h`
UNION ALL
SELECT
  signal_id,
  signal_type,
  signal_window,
  "market" AS entity_type,
  market_ticker AS entity_id,
  title,
  market_family,
  score,
  severity,
  signal_ts,
  supporting_metrics_json AS metrics_json,
  explanation_short
FROM `YOUR_PROJECT_ID.kalshi_signal.vw_signal_liquidity_deterioration_latest`
UNION ALL
SELECT
  signal_id,
  signal_type,
  signal_window,
  "market" AS entity_type,
  market_ticker AS entity_id,
  title,
  market_family,
  score,
  severity,
  signal_ts,
  supporting_metrics_json AS metrics_json,
  explanation_short
FROM `YOUR_PROJECT_ID.kalshi_signal.vw_signal_open_interest_change_12h`
UNION ALL
SELECT
  signal_id,
  signal_type,
  signal_window,
  "event" AS entity_type,
  event_key AS entity_id,
  title,
  market_family,
  score,
  severity,
  signal_ts,
  supporting_metrics_json AS metrics_json,
  explanation_short
FROM `YOUR_PROJECT_ID.kalshi_signal.vw_signal_event_reactions_3h`
UNION ALL
SELECT
  signal_id,
  signal_type,
  signal_window,
  "event" AS entity_type,
  event_key AS entity_id,
  title,
  market_family,
  score,
  severity,
  signal_ts,
  supporting_metrics_json AS metrics_json,
  explanation_short
FROM `YOUR_PROJECT_ID.kalshi_signal.vw_signal_cross_market_inconsistencies`;
