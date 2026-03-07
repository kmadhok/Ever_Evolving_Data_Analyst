-- BigQuery Standard SQL
-- Data-science-oriented dashboard views for role-based /ds route.
-- Project: brainrot-453319

CREATE OR REPLACE VIEW `brainrot-453319.kalshi_dash.vw_ds_feature_drift_24h` AS
WITH tagged AS (
  SELECT
    market_ticker,
    created_time,
    COALESCE(yes_price_dollars, 0) AS yes_price_dollars,
    COALESCE(count_contracts, 0) AS count_contracts,
    CASE
      WHEN created_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 6 HOUR) THEN "last_6h"
      WHEN created_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 12 HOUR)
        AND created_time < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 6 HOUR) THEN "prev_6h"
      ELSE NULL
    END AS win
  FROM `brainrot-453319.kalshi_core.trade_prints_core`
  WHERE created_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 12 HOUR)
),
agg AS (
  SELECT
    market_ticker,
    COUNTIF(win = "last_6h") AS trades_last_6h,
    COUNTIF(win = "prev_6h") AS trades_prev_6h,
    AVG(IF(win = "last_6h", yes_price_dollars, NULL)) AS mean_yes_price_last_6h,
    AVG(IF(win = "prev_6h", yes_price_dollars, NULL)) AS mean_yes_price_prev_6h,
    SUM(IF(win = "last_6h", count_contracts, 0)) AS contracts_last_6h,
    SUM(IF(win = "prev_6h", count_contracts, 0)) AS contracts_prev_6h
  FROM tagged
  WHERE win IS NOT NULL
  GROUP BY market_ticker
)
SELECT
  market_ticker,
  trades_last_6h,
  trades_prev_6h,
  mean_yes_price_last_6h,
  mean_yes_price_prev_6h,
  SAFE_DIVIDE(mean_yes_price_last_6h - mean_yes_price_prev_6h, NULLIF(mean_yes_price_prev_6h, 0))
    AS yes_price_pct_shift,
  SAFE_DIVIDE(contracts_last_6h - contracts_prev_6h, NULLIF(contracts_prev_6h, 0))
    AS contracts_pct_shift
FROM agg
WHERE trades_last_6h > 0;

CREATE OR REPLACE VIEW `brainrot-453319.kalshi_dash.vw_ds_label_coverage_24h` AS
SELECT
  COUNT(*) AS total_trades_24h,
  COUNT(DISTINCT market_ticker) AS active_markets_24h,
  SAFE_DIVIDE(COUNTIF(yes_price_dollars IS NOT NULL), COUNT(*)) AS yes_price_coverage_ratio,
  SAFE_DIVIDE(COUNTIF(count_contracts IS NOT NULL), COUNT(*)) AS contracts_coverage_ratio,
  SAFE_DIVIDE(COUNTIF(taker_side IS NOT NULL AND taker_side != ""), COUNT(*)) AS non_null_taker_side_ratio,
  CURRENT_TIMESTAMP() AS generated_at
FROM `brainrot-453319.kalshi_core.trade_prints_core`
WHERE created_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR);

CREATE OR REPLACE VIEW `brainrot-453319.kalshi_dash.vw_ds_retrain_signal_24h` AS
WITH per_market AS (
  SELECT
    market_ticker,
    COUNT(*) AS trade_count_24h,
    SUM(COALESCE(count_contracts, 0)) AS contracts_traded_24h,
    AVG(COALESCE(yes_price_dollars, 0)) AS yes_price_mean,
    STDDEV_POP(COALESCE(yes_price_dollars, 0)) AS yes_price_stddev
  FROM `brainrot-453319.kalshi_core.trade_prints_core`
  WHERE created_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
  GROUP BY market_ticker
)
SELECT
  market_ticker,
  trade_count_24h,
  contracts_traded_24h,
  yes_price_mean,
  yes_price_stddev,
  SAFE_MULTIPLY(
    SAFE_DIVIDE(yes_price_stddev, NULLIF(yes_price_mean, 0)),
    LOG10(trade_count_24h + 1)
  ) AS retrain_priority_score
FROM per_market
WHERE trade_count_24h > 3;
