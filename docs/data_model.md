# Kalshi Data Model Reference

## Architecture

Medallion architecture with five layers:

```
Kalshi REST API
      │
      ▼
┌─────────────────────────────────────────────────────┐
│  RAW (kalshi_raw)                                   │
│  Append-only JSON blobs, partitioned by ingestion_  │
│  date. One table per API endpoint.                  │
│  ─ api_call_log, events_raw, markets_raw,           │
│    trades_raw, orderbooks_raw                       │
└─────────────────────┬───────────────────────────────┘
                      │ JSON extraction (INSERT)
                      ▼
┌─────────────────────────────────────────────────────┐
│  STAGING (kalshi_stg)                               │
│  Parsed/normalized fields. Still append-only.       │
│  ─ events_stg, market_snapshots_stg,                │
│    trades_stg, orderbook_levels_stg                 │
└─────────────────────┬───────────────────────────────┘
                      │ MERGE / dedupe (quality-gated)
                      ▼
┌─────────────────────────────────────────────────────┐
│  CORE (kalshi_core)                                 │
│  Deduped business tables. Source of truth.          │
│  ─ events_dim, market_state_core,                   │
│    trade_prints_core, kpi_5m, dashboard_events      │
└──────────┬──────────────────────┬───────────────────┘
           │                      │
           ▼                      ▼
┌─────────────────────┐  ┌───────────────────────────┐
│  DASHBOARD           │  │  SIGNAL                   │
│  (kalshi_dash)       │  │  (kalshi_signal)          │
│  Read-only views     │  │  Analytics views          │
│  for UI tiles        │  │  UNION'd into             │
│                      │  │  vw_signal_feed_latest    │
└──────────────────────┘  └───────────────────────────┘
```

Operations metadata lives in `kalshi_ops` (rate limits, quality results, signal runs, reports).

---

## Join Keys

Three primary keys connect the entire model:

| Key | Granularity | Example | Connects |
|-----|-------------|---------|----------|
| `event_ticker` | One per prediction event | `HIGHTEMP-25FEB01` | events ↔ markets |
| `market_ticker` | One per tradeable contract | `HIGHTEMP-25FEB01-T90.0` | markets ↔ trades ↔ orderbooks |
| `trade_id` | One per executed trade | `abc-123-def` | trade deduplication |

Additional keys:
- `series_ticker` — groups related events (e.g., all `HIGHTEMP` events). Used in kpi_5m.
- `call_id` / `source_call_id` — links raw payloads to the API call that fetched them.
- `snapshot_ts` — timestamp of each data snapshot; used with `market_ticker` for orderbook joins.

### Primary Join Chain

```
events_dim
    │  event_ticker
    ▼
market_state_core
    │  market_ticker
    ├──────────────────┐
    ▼                  ▼
trade_prints_core   orderbook_levels_stg
```

All joins are **LEFT JOIN** because trades can arrive before market metadata is hydrated, and event titles backfill asynchronously.

---

## Table Schemas

### Raw Layer (kalshi_raw)

#### api_call_log
Audit log of every Kalshi API request.

| Column | Type | Notes |
|--------|------|-------|
| call_id | STRING | **PK** |
| endpoint | STRING | e.g., `/markets`, `/trades` |
| request_url | STRING | Full URL with query params |
| request_params | JSON | Parsed query parameters |
| response_status | INT64 | HTTP status (200, 429, etc.) |
| response_headers | JSON | Response headers |
| response_cursor | STRING | Pagination cursor |
| fetched_at | TIMESTAMP | When call was made |
| ingestion_date | DATE | **Partition key** |

Clustered by: `endpoint`

#### events_raw
Raw event metadata blobs.

| Column | Type | Notes |
|--------|------|-------|
| call_id | STRING | FK → api_call_log |
| event_ticker | STRING | Event identifier |
| series_ticker | STRING | Series identifier |
| title | STRING | Event title |
| sub_title | STRING | Event subtitle |
| status | STRING | open, closed, settled |
| last_updated_ts | TIMESTAMP | |
| api_payload | JSON | Full API response |
| fetched_at | TIMESTAMP | |
| ingestion_date | DATE | **Partition key** |

Clustered by: `series_ticker`, `event_ticker`

#### markets_raw
Raw market snapshot blobs.

| Column | Type | Notes |
|--------|------|-------|
| call_id | STRING | FK → api_call_log |
| market_ticker | STRING | Market identifier |
| event_ticker | STRING | FK → events_raw |
| series_ticker | STRING | Series identifier |
| status | STRING | open, active, closed |
| close_time | TIMESTAMP | Market expiration |
| api_payload | JSON | Full market snapshot |
| fetched_at | TIMESTAMP | |
| ingestion_date | DATE | **Partition key** |

Clustered by: `series_ticker`, `status`

#### trades_raw
Raw trade execution blobs.

| Column | Type | Notes |
|--------|------|-------|
| call_id | STRING | FK → api_call_log |
| trade_id | STRING | Unique trade ID |
| market_ticker | STRING | FK → markets_raw |
| created_time | TIMESTAMP | Trade execution time |
| api_payload | JSON | Full trade JSON |
| fetched_at | TIMESTAMP | |
| ingestion_date | DATE | **Partition key** |

Clustered by: `market_ticker`

#### orderbooks_raw
Raw orderbook snapshot blobs.

| Column | Type | Notes |
|--------|------|-------|
| call_id | STRING | FK → api_call_log |
| market_ticker | STRING | FK → markets_raw |
| api_payload | JSON | Full orderbook JSON |
| fetched_at | TIMESTAMP | |
| ingestion_date | DATE | **Partition key** |

Clustered by: `market_ticker`

---

### Staging Layer (kalshi_stg)

#### events_stg
Parsed event fields extracted from JSON.

| Column | Type | Notes |
|--------|------|-------|
| event_ticker | STRING | Event identifier |
| series_ticker | STRING | Series identifier |
| title | STRING | Event title |
| sub_title | STRING | |
| status | STRING | |
| last_updated_ts | TIMESTAMP | |
| snapshot_ts | TIMESTAMP | When snapshot was taken |
| source_call_id | STRING | FK → api_call_log |
| ingestion_date | DATE | **Partition key** |

Clustered by: `series_ticker`, `event_ticker`

#### market_snapshots_stg
Parsed market state at each snapshot.

| Column | Type | Notes |
|--------|------|-------|
| market_ticker | STRING | Market identifier |
| event_ticker | STRING | FK → events_stg |
| series_ticker | STRING | |
| status | STRING | |
| yes_bid_dollars | NUMERIC | Best YES bid |
| yes_ask_dollars | NUMERIC | Best YES ask |
| no_bid_dollars | NUMERIC | Best NO bid |
| no_ask_dollars | NUMERIC | Best NO ask |
| last_price_dollars | NUMERIC | Last traded price |
| volume_dollars | NUMERIC | Cumulative volume |
| open_interest_dollars | NUMERIC | Open interest |
| close_time | TIMESTAMP | Market expiration |
| snapshot_ts | TIMESTAMP | |
| source_call_id | STRING | FK → api_call_log |
| ingestion_date | DATE | **Partition key** |

Clustered by: `series_ticker`, `status`

#### trades_stg
Parsed trade executions.

| Column | Type | Notes |
|--------|------|-------|
| trade_id | STRING | **PK** |
| market_ticker | STRING | FK → market_snapshots_stg |
| yes_price_dollars | NUMERIC | YES side price |
| no_price_dollars | NUMERIC | NO side price |
| count_contracts | NUMERIC | Contracts traded |
| taker_side | STRING | `yes` or `no` |
| created_time | TIMESTAMP | Trade execution time |
| snapshot_ts | TIMESTAMP | |
| source_call_id | STRING | FK → api_call_log |
| ingestion_date | DATE | **Partition key** |

Clustered by: `market_ticker`

#### orderbook_levels_stg
Unnested orderbook bid/ask levels.

| Column | Type | Notes |
|--------|------|-------|
| market_ticker | STRING | FK → market_snapshots_stg |
| side | STRING | `yes` or `no` |
| price_dollars | NUMERIC | Price level |
| quantity_contracts | NUMERIC | Quantity at level |
| level_rank | INT64 | 1 = top of book |
| snapshot_ts | TIMESTAMP | |
| source_call_id | STRING | FK → api_call_log |
| ingestion_date | DATE | **Partition key** |

Clustered by: `market_ticker`, `side`

---

### Core Layer (kalshi_core)

#### events_dim
Slowly-changing event dimension. One current row per event.

| Column | Type | Notes |
|--------|------|-------|
| event_ticker | STRING | **PK** (with is_latest) |
| series_ticker | STRING | |
| title | STRING | Event title |
| sub_title | STRING | |
| status | STRING | |
| last_updated_ts | TIMESTAMP | |
| last_seen_snapshot_ts | TIMESTAMP | Most recent snapshot |
| is_latest | BOOL | TRUE = current version |
| updated_at | TIMESTAMP | |
| ingestion_date | DATE | **Partition key** |

Clustered by: `series_ticker`, `event_ticker`

**Dedup strategy**: MERGE on `event_ticker`. Periodically compacted via CREATE OR REPLACE with `ROW_NUMBER() OVER (PARTITION BY event_ticker ORDER BY last_seen_snapshot_ts DESC) = 1`.

#### market_state_core
Time-series of market snapshots. Schema matches market_snapshots_stg plus `is_latest`.

| Column | Type | Notes |
|--------|------|-------|
| market_ticker | STRING | Market identifier |
| event_ticker | STRING | FK → events_dim |
| series_ticker | STRING | |
| status | STRING | |
| yes_bid_dollars | NUMERIC | |
| yes_ask_dollars | NUMERIC | |
| no_bid_dollars | NUMERIC | |
| no_ask_dollars | NUMERIC | |
| last_price_dollars | NUMERIC | |
| volume_dollars | NUMERIC | |
| open_interest_dollars | NUMERIC | |
| close_time | TIMESTAMP | |
| snapshot_ts | TIMESTAMP | |
| is_latest | BOOL | TRUE = most recent snapshot |
| ingestion_date | DATE | **Partition key** |

Clustered by: `series_ticker`, `market_ticker`

**Dedup strategy**: INSERT all snapshots, then periodic CTAS with `ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY snapshot_ts DESC, ingestion_date DESC, close_time DESC) = 1` to set `is_latest`.

#### trade_prints_core
Deduplicated trade executions. Schema matches trades_stg.

| Column | Type | Notes |
|--------|------|-------|
| trade_id | STRING | **PK** |
| market_ticker | STRING | FK → market_state_core |
| yes_price_dollars | NUMERIC | |
| no_price_dollars | NUMERIC | |
| count_contracts | NUMERIC | |
| taker_side | STRING | `yes` or `no` |
| created_time | TIMESTAMP | Trade execution time |
| snapshot_ts | TIMESTAMP | |
| ingestion_date | DATE | **Partition key** |

Clustered by: `market_ticker`

**Dedup strategy**: MERGE on `trade_id`.

#### kpi_5m
Five-minute aggregated KPIs.

| Column | Type | Notes |
|--------|------|-------|
| kpi_ts | TIMESTAMP | 5-min bucket (`TIMESTAMP_SECONDS(300 * DIV(...))`) |
| series_ticker | STRING | NULL = all series |
| open_market_count | INT64 | |
| total_volume_dollars | NUMERIC | |
| total_open_interest_dollars | NUMERIC | |
| trade_count_lookback | INT64 | Trades in lookback window |
| updated_at | TIMESTAMP | |

Partitioned by: `DATE(kpi_ts)`. Clustered by: `series_ticker`.

**Strategy**: MERGE on `kpi_ts + series_ticker` every DAG run.

#### dashboard_events
User interaction tracking.

| Column | Type | Notes |
|--------|------|-------|
| event_ts | TIMESTAMP | |
| user_id | STRING | |
| dashboard_id | STRING | |
| action | STRING | view, filter, export |
| panel_id | STRING | Tile/widget ID |
| filter_json | JSON | |
| session_id | STRING | |
| ingestion_date | DATE | **Partition key** |

Clustered by: `dashboard_id`, `action`

---

### Dashboard Views (kalshi_dash)

All are read-only SELECT views over core tables. Common pattern:

```sql
SELECT ...
FROM trade_prints_core t
LEFT JOIN market_state_core m USING (market_ticker)
LEFT JOIN events_dim e USING (event_ticker)
WHERE t.created_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL <window>)
```

| View | Purpose | Time Window |
|------|---------|-------------|
| vw_pipeline_heartbeat | Pipeline health (latest trade age, quality failures) | Real-time |
| vw_quality_checks_24h | Quality check results | 24h |
| vw_top_active_markets_24h | Most active markets by volume | 24h |
| vw_trade_flow_6h | Trade flow by series | 6h |
| vw_most_traded_markets_60m | Most traded markets | 60m |
| vw_price_movers_60m | Biggest price changes | 60m |
| vw_simple_side_summary_24h | Side-level market view with favorite/underdog | 24h |
| vw_consumer_matchup_view | Consumer-friendly matchup display | 24h |
| vw_orderbook_top_snapshot | Top-of-book orderbook levels | Latest |

---

### Signal Views (kalshi_signal)

Analytics views that detect market anomalies. All feed into `vw_signal_feed_latest`.

| View | Signal Type | Window | Key Metric |
|------|-------------|--------|------------|
| vw_signal_probability_shifts_24h | probability_shift | 24h | `last_yes_price - first_yes_price` >= 0.08 |
| vw_signal_volume_spikes_6h | volume_spike | 6h | `contracts_6h / baseline_6h` >= 2.0 |
| vw_signal_volatility_spikes_1h | volatility_spike | 1h | `stddev_1h / stddev_24h` >= 1.5 |
| vw_signal_liquidity_deterioration_latest | liquidity_deterioration | Latest | `1 - (yes_bid + no_bid)` >= 0.15 |
| vw_signal_open_interest_change_12h | open_interest_change | 12h | OI delta by market |
| vw_signal_cross_market_inconsistencies | cross_market_inconsistency | Latest | `SUM(yes_prices) > 1.0` for mutually exclusive markets |
| vw_signal_event_reactions_3h | event_reaction | 3h | Trade surge after event status change |

#### vw_signal_feed_latest
UNION ALL of all signal views with common schema:

| Column | Type |
|--------|------|
| signal_id | STRING |
| signal_type | STRING |
| signal_window | STRING |
| entity_type | STRING |
| entity_id | STRING |
| title | STRING |
| market_family | STRING |
| score | FLOAT64 |
| severity | STRING (LOW/MEDIUM/HIGH) |
| signal_ts | TIMESTAMP |
| explanation_short | STRING |
| metrics_json | JSON |

Helper views used by signal views:
- `vw_market_latest` — latest snapshot per market (`is_latest = TRUE`)
- `vw_event_latest` — latest event per ticker (`is_latest = TRUE`)

---

### Operations Layer (kalshi_ops)

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| rate_limit_log | Rate-limit decisions per run | run_id, effective_read_rps, read_budget_per_run |
| quality_results | Quality check pass/fail per run | run_id, check_name, status, metric_value, threshold_value |
| schema_change_proposals | Schema evolution proposals | proposal_id, target_table, change_sql, status |
| schema_change_decisions | Proposal approvals | proposal_id, decision, decided_by |
| signal_runs | Signal generation tracking | run_id, signal_count, signal_type_counts |
| market_intelligence_reports | Periodic report snapshots | report_id, report_type, summary_json |
| dashboard_spec_versions | Dashboard spec version history | dashboard_id, version_id, spec_json, status |

---

## Common Query Patterns

### Pattern 1: Trade analytics with event enrichment

```sql
SELECT
  t.market_ticker,
  COALESCE(e.title, m.event_ticker) AS event_title,
  COUNT(*) AS trade_count,
  SUM(t.count_contracts) AS total_contracts,
  AVG(t.yes_price_dollars) AS avg_price
FROM kalshi_core.trade_prints_core t
LEFT JOIN kalshi_core.market_state_core m
  ON m.market_ticker = t.market_ticker AND m.is_latest = TRUE
LEFT JOIN kalshi_core.events_dim e
  ON e.event_ticker = m.event_ticker AND e.is_latest = TRUE
WHERE t.created_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
GROUP BY 1, 2
ORDER BY trade_count DESC
LIMIT 20
```

### Pattern 2: Market snapshot with orderbook

```sql
SELECT
  m.market_ticker,
  m.last_price_dollars,
  m.volume_dollars,
  m.open_interest_dollars,
  o_yes.price_dollars AS best_yes_bid,
  o_no.price_dollars AS best_no_bid
FROM kalshi_core.market_state_core m
LEFT JOIN kalshi_stg.orderbook_levels_stg o_yes
  ON o_yes.market_ticker = m.market_ticker
  AND o_yes.side = 'yes' AND o_yes.level_rank = 1
LEFT JOIN kalshi_stg.orderbook_levels_stg o_no
  ON o_no.market_ticker = m.market_ticker
  AND o_no.side = 'no' AND o_no.level_rank = 1
WHERE m.is_latest = TRUE
```

### Pattern 3: Event title fallback (when join fails)

```sql
COALESCE(
  e.title,
  m.event_ticker,
  REGEXP_EXTRACT(t.market_ticker, r'^(.*)-[^-]+$')
) AS event_title
```

`market_ticker` format: `{event_ticker}-{side_label}` (e.g., `HIGHTEMP-25FEB01-T90.0`).
The REGEX strips the last `-segment` to recover the event_ticker.

### Pattern 4: Latest row per entity (dedup)

```sql
SELECT *
FROM kalshi_core.market_state_core
WHERE is_latest = TRUE

-- Or equivalently via window function:
SELECT * FROM (
  SELECT *,
    ROW_NUMBER() OVER (
      PARTITION BY market_ticker
      ORDER BY snapshot_ts DESC
    ) AS rn
  FROM kalshi_core.market_state_core
)
WHERE rn = 1
```

### Pattern 5: 5-minute KPI aggregation

```sql
SELECT
  TIMESTAMP_SECONDS(300 * DIV(UNIX_SECONDS(CURRENT_TIMESTAMP()), 300)) AS kpi_ts,
  series_ticker,
  COUNT(DISTINCT market_ticker) AS open_market_count,
  SUM(volume_dollars) AS total_volume_dollars
FROM kalshi_core.market_state_core
WHERE status IN ('open', 'active') AND is_latest = TRUE
GROUP BY 1, 2
```

---

## Quality Gates

Three checks gate the staging → core publish:

| Check | Metric | Threshold | Fails When |
|-------|--------|-----------|------------|
| freshness_minutes | Max age of market snapshots | <= 15 min | Data is stale |
| null_market_ticker_ratio | Null rate in market_ticker | <= 0.01 | Bad parsing |
| duplicate_trade_id_ratio | Duplicate trade_id count | <= 0.01 | Dedup failure |

Quality gate results are logged to `kalshi_ops.quality_results`.
