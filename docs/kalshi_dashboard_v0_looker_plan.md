# Kalshi Dashboard v0 (Looker Studio Plan)

This is the starter dashboard for your autonomous pipeline.  
Data source: BigQuery project `brainrot-453319`, dataset `kalshi_dash` views from `004_kalshi_dashboard_views.sql`.

## Page 1: Pipeline Health

Use `vw_pipeline_heartbeat` (scorecards):
- `minutes_since_latest_market_snapshot`
- `minutes_since_latest_trade`
- `minutes_since_latest_orderbook_level`
- `quality_failures_last_60m`
- `api_calls_last_60m`

Use `vw_quality_checks_24h` (table):
- Dimensions: `checked_at`, `run_id`, `check_name`, `status`
- Metrics: `metric_value`, `threshold_value`
- Default sort: `checked_at DESC`

Use `vw_ingestion_throughput_24h` (time series):
- Dimension: `ts_minute`
- Breakdown: `endpoint`
- Metric: `api_call_count`

Use `vw_endpoint_gap_24h` (time series):
- Dimension: `ts_minute`
- Breakdown: `endpoint`
- Metric: `avg_gap_seconds`

## Page 2: Market & Trade Activity

Use `vw_kpi_5m_24h` (time series):
- Dimension: `kpi_ts`
- Metrics: `total_volume_dollars`, `total_open_interest_dollars`, `trade_count_lookback`
- Breakdown: `series_ticker`

Use `vw_top_active_markets_24h` (table):
- Dimensions: `market_ticker`, `status`, `snapshot_ts`
- Metrics: `last_price_dollars`, `volume_dollars`, `open_interest_dollars`
- Default sort: `volume_dollars DESC`

Use `vw_trade_flow_6h` (stacked area or bars):
- Dimension: `ts_minute`
- Breakdown: `taker_side`
- Metrics: `trade_count`, `contracts_traded`

Use `vw_most_traded_markets_60m` (table):
- Dimension: `market_ticker`
- Metrics: `trade_count`, `contracts_traded`, `avg_yes_price_dollars`
- Default sort: `contracts_traded DESC`

## Page 3: Price Discovery

Use `vw_price_movers_60m` (table):
- Dimension: `market_ticker`
- Metrics: `first_yes_price`, `last_yes_price`, `abs_move`, `pct_move`
- Default sort: `ABS(abs_move) DESC`

Use `vw_orderbook_top_snapshot` (table):
- Dimension: `market_ticker`
- Metrics: `best_yes_price`, `best_no_price`, `top_yes_qty`, `top_no_qty`
- Additional column: `snapshot_ts`

## Global Controls

Add report-level controls:
- Date range control: default `Last 24 hours`
- Text filter: `market_ticker`
- Dropdown filter: `endpoint` (on ingestion/latency charts)
- Dropdown filter: `taker_side` (on trade charts)

## Alert Rules (Initial)

You can implement these as scheduled queries or simple monitor jobs:
- `minutes_since_latest_market_snapshot > 15`
- `minutes_since_latest_trade > 15`
- `quality_failures_last_60m > 0`
- `api_calls_last_60m < 10`

## Build Order (Fastest Path)

1. Run `sql/bigquery/004_kalshi_dashboard_views.sql`.
2. In Looker Studio, create one BigQuery data source per view or a single blended source set.
3. Build Page 1 first (operational confidence).
4. Build Pages 2-3.
5. Add alerting checks after 24h of baseline behavior.
