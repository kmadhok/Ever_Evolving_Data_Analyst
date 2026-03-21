# Kalshi Data Guide

> A practical reference for understanding the Kalshi prediction-market data in our
> BigQuery warehouse, how it differs from traditional sports betting, and where the
> current gaps are.
>
> **Last updated:** 2026-03-21
> **BigQuery project:** `brainrot-453319`

---

## 1. Kalshi vs Traditional Sports Betting

### 1.1 The core concept

Traditional sportsbooks offer **odds** on outcomes. Kalshi offers **binary
contracts** that trade like mini-stocks.

Every Kalshi contract is a yes/no question that settles at exactly **$1.00**
(YES was correct) or **$0.00** (YES was wrong). The current trading price *is*
the market's implied probability.

| Concept | Sportsbook | Kalshi |
|---------|-----------|--------|
| Format | Odds (e.g., -300 / +250) | Price (e.g., $0.75 / $0.25) |
| What you buy | A bet on a team/outcome | A YES or NO contract |
| Payout | Varies by odds | Always $1.00 if correct, $0.00 if wrong |
| Implied probability | Derived from odds | **Is** the price |
| Two-sided? | One bet covers one side | YES + NO are two sides of the same contract |

### 1.2 Mapping familiar bet types

#### Money line (who wins?)

A sportsbook lists:

```
Dallas   +250   (implied ~28.6%)
Boston   -300   (implied ~75.0%)
```

Kalshi creates **one contract**: *"Will Dallas win?"*

| Action | What it means | Cost | Profit if right |
|--------|--------------|------|-----------------|
| Buy YES at $0.29 | Betting on Dallas | $0.29 | $0.71 |
| Buy NO at $0.71 | Betting on Boston | $0.71 | $0.29 |

There is no separate "Will Boston win?" contract for a head-to-head game. Buying
NO on Dallas **is** the Boston bet. YES + NO prices always sum to ~$1.00.

#### Spread

A sportsbook lists:

```
Dallas +5.5   -110
Boston -5.5   -110
```

Kalshi creates **a separate yes/no contract for each line**:

```
"Dallas at Boston: Spread"
  market: ...-T3.5   → "Will the margin be > 3.5?"   YES $0.55 / NO $0.45
  market: ...-T5.5   → "Will the margin be > 5.5?"   YES $0.42 / NO $0.58
  market: ...-T7.5   → "Will the margin be > 7.5?"   YES $0.31 / NO $0.69
  market: ...-T10.5  → "Will the margin be > 10.5?"  YES $0.18 / NO $0.82
```

You can buy YES on one threshold and NO on another — something a traditional
sportsbook doesn't allow.

#### Over/under (total points)

Same pattern as spread. Kalshi creates one contract per total-point threshold:

```
"Dallas at Boston: Total Points"
  market: ...-T210.5  → "Will the total exceed 210.5?"  YES $0.60 / NO $0.40
  market: ...-T215.5  → "Will the total exceed 215.5?"  YES $0.48 / NO $0.52
  market: ...-T220.5  → "Will the total exceed 220.5?"  YES $0.33 / NO $0.67
```

#### Futures / multi-outcome

For questions with many possible outcomes ("Who wins the NBA championship?"),
Kalshi creates **one yes/no contract per candidate**:

```
"Will Boston win the championship?"       YES $0.22 / NO $0.78
"Will Oklahoma City win?"                 YES $0.18 / NO $0.82
"Will Dallas win?"                        YES $0.08 / NO $0.92
...one contract per team
```

The YES prices across all candidates should sum to ~$1.00 (since exactly one
wins). If they sum to more, there's an arbitrage opportunity — this is what our
`vw_signal_cross_market_inconsistencies` signal detects.

### 1.3 How one NBA game maps to Kalshi events

A single game (e.g., Dallas at Boston, March 6) spawns events across many
series:

| Series | Event example | What it covers |
|--------|--------------|----------------|
| `KXNBAGAME` | `KXNBAGAME-26MAR06DALBOS` | Game winner |
| `KXNBASPREAD` | `KXNBASPREAD-26MAR06DALBOS` | Point spread (multiple thresholds) |
| `KXNBATOTAL` | `KXNBATOTAL-26MAR06DALBOS` | Total points (multiple thresholds) |
| `KXNBA1HWINNER` | `KXNBA1HWINNER-26MAR06DALBOS` | First half winner |
| `KXNBA1HSPREAD` | `KXNBA1HSPREAD-26MAR06DALBOS` | First half spread |
| `KXNBA1HTOTAL` | `KXNBA1HTOTAL-26MAR06DALBOS` | First half total |
| `KXNBA2HWINNER` | `KXNBA2HWINNER-26MAR06DALBOS` | Second half winner |
| `KXNBA2D` | `KXNBA2D-26MAR06DALBOS` | Double-doubles |
| `KXNBA3D` | `KXNBA3D-26MAR06DALBOS` | Triple-doubles |
| `KXNBATEAMTOTAL` | `KXNBATEAMTOTAL-26MAR06DALBOS` | Team-level totals |
| `KXNBAMENTION` | `KXNBAMENTION-26MAR06DALBOS` | Announcer mentions |

**Key takeaway:** The game identifier (e.g., `26MAR06DALBOS`) is embedded in the
event ticker. To find *all* contracts for a game, search across series:

```sql
SELECT e.series_ticker, e.event_ticker, e.title,
       m.market_ticker, m.yes_bid_dollars, m.yes_ask_dollars
FROM kalshi_core.events_dim e
LEFT JOIN kalshi_core.market_state_core m
  ON m.event_ticker = e.event_ticker AND m.is_latest = TRUE
WHERE e.is_latest = TRUE
  AND e.event_ticker LIKE '%DALBOS%'
ORDER BY e.series_ticker, m.market_ticker
```

### 1.4 Summary of differences

| | Traditional sportsbook | Kalshi prediction market |
|---|---|---|
| Two-team game | Two sides listed together | One YES/NO contract (NO = other team) |
| Spread | One line offered | Multiple threshold contracts |
| Over/under | One line offered | Multiple threshold contracts |
| Futures | One market, pick a winner | One YES/NO contract per candidate |
| Prices move? | Odds adjust pre-game | Contracts trade continuously like stocks |
| Can you sell early? | Cash-out (limited) | Sell your contract anytime before settlement |
| Live/in-game | Some books offer live odds | Contracts trade during game |

---

## 2. BigQuery Data Asset Inventory

### 2.1 Architecture overview

```
Kalshi REST API
      |
      v
+------------------------------------------------------+
|  RAW (kalshi_raw)        5 tables    ~793 MB total    |
|  Append-only JSON blobs, partitioned by ingestion_date|
+----------------------------+-------------------------+
                             | JSON extraction
                             v
+------------------------------------------------------+
|  STG (kalshi_stg)        4 tables    ~119 MB total    |
|  Parsed/normalized fields. Still append-only.         |
+----------------------------+-------------------------+
                             | MERGE / dedupe
                             v
+------------------------------------------------------+
|  CORE (kalshi_core)      5 tables    ~99 MB total     |
|  Deduped business tables. Source of truth.            |
+--------------+-------------------+-------------------+
               |                   |
               v                   v
+-----------------------+  +---------------------------+
| DASHBOARD             |  | SIGNAL                    |
| (kalshi_dash)         |  | (kalshi_signal)           |
| 18 read-only views    |  | 10 analytics views        |
+-----------------------+  +---------------------------+

Operations metadata: kalshi_ops (11 tables)
```

**Pipeline cadence:** Every 5 minutes (Airflow DAG).
**Data collection window:** March 6 – March 21, 2026 (15 days so far).
**API endpoints hit:** `/markets`, `/markets/trades`, `/events/{ticker}`, `/markets/{ticker}/orderbook`.

### 2.2 Raw layer — `kalshi_raw`

Append-only JSON blobs. One table per API endpoint.

| Table | Rows | Size | Partition | Clustered by | Description |
|-------|-----:|-----:|-----------|-------------|-------------|
| `api_call_log` | 55,432 | 27.2 MB | `ingestion_date` | `endpoint` | Audit log of every API request. Tracks URL, status, cursor, headers. |
| `events_raw` | 32,317 | 9.2 MB | `ingestion_date` | `series_ticker`, `event_ticker` | Event metadata blobs (title, status, sub_title). |
| `markets_raw` | 315,600 | 696.3 MB | `ingestion_date` | `series_ticker`, `status` | **Largest table.** Full market snapshot JSON (prices, volume, OI). |
| `trades_raw` | 210,800 | 59.4 MB | `ingestion_date` | `market_ticker` | Trade execution JSON (trade_id, price, side, contracts). |
| `orderbooks_raw` | 13,150 | 1.5 MB | `ingestion_date` | `market_ticker` | Orderbook snapshot JSON (bid/ask levels, depth). |

### 2.3 Staging layer — `kalshi_stg`

JSON fields extracted into typed columns. Still append-only (no dedup).

| Table | Rows | Size | Key columns | Description |
|-------|-----:|-----:|-------------|-------------|
| `events_stg` | 32,950 | 4.8 MB | `event_ticker`, `series_ticker`, `title`, `status` | Parsed event fields. |
| `market_snapshots_stg` | 313,800 | 78.7 MB | `market_ticker`, `yes_bid_dollars`, `yes_ask_dollars`, `volume_dollars`, `snapshot_ts` | Parsed market state at each snapshot. |
| `trades_stg` | 208,800 | 35.7 MB | `trade_id`, `market_ticker`, `yes_price_dollars`, `taker_side`, `count_contracts` | Parsed trade executions. |
| `orderbook_levels_stg` | 2,062 | 0.3 MB | `market_ticker`, `side`, `price_dollars`, `quantity_contracts`, `level_rank` | Unnested bid/ask levels. |

### 2.4 Core layer — `kalshi_core`

Deduplicated source-of-truth tables.

#### `events_dim` — 16,116 rows, 1.9 MB

Slowly-changing dimension. One current row per event (filtered by `is_latest = TRUE`).

| Column | Type | Notes |
|--------|------|-------|
| `event_ticker` | STRING | **PK** (e.g., `KXNBAGAME-26MAR06DALBOS`) |
| `series_ticker` | STRING | Groups related events (e.g., `KXNBAGAME`) |
| `title` | STRING | Human-readable (e.g., "Dallas at Boston") |
| `sub_title` | STRING | Extra context (e.g., "DAL at BOS (Mar 6)") |
| `status` | STRING | `open`, `closed`, `settled` |
| `is_latest` | BOOL | `TRUE` = current version |

**Dedup:** MERGE on `event_ticker`, compacted via `ROW_NUMBER() OVER (PARTITION BY event_ticker ORDER BY last_seen_snapshot_ts DESC) = 1`.

#### `market_state_core` — 309,000 rows, 66.5 MB

Time-series of market snapshots. **The main fact table.**

| Column | Type | Notes |
|--------|------|-------|
| `market_ticker` | STRING | The tradeable contract |
| `event_ticker` | STRING | FK to `events_dim` |
| `series_ticker` | STRING | **Currently NULL for all rows** (see Gaps) |
| `yes_bid_dollars` | NUMERIC | Best YES bid price |
| `yes_ask_dollars` | NUMERIC | Best YES ask price |
| `no_bid_dollars` | NUMERIC | Best NO bid price |
| `no_ask_dollars` | NUMERIC | Best NO ask price |
| `last_price_dollars` | NUMERIC | Last traded price |
| `volume_dollars` | NUMERIC | Cumulative volume |
| `open_interest_dollars` | NUMERIC | Outstanding contracts |
| `snapshot_ts` | TIMESTAMP | When snapshot was captured |
| `is_latest` | BOOL | `TRUE` = most recent snapshot for this market |

**Dedup:** INSERT all snapshots, periodic CTAS to set `is_latest` via `ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY snapshot_ts DESC) = 1`.

**Join pattern (most common query):**

```sql
SELECT t.market_ticker,
       COALESCE(e.title, m.event_ticker) AS event_title,
       m.yes_bid_dollars, m.last_price_dollars
FROM kalshi_core.trade_prints_core t
LEFT JOIN kalshi_core.market_state_core m
  ON m.market_ticker = t.market_ticker AND m.is_latest = TRUE
LEFT JOIN kalshi_core.events_dim e
  ON e.event_ticker = m.event_ticker AND e.is_latest = TRUE
```

#### `trade_prints_core` — 206,000 rows, 27.7 MB

Every individual trade, deduplicated by `trade_id`.

| Column | Type | Notes |
|--------|------|-------|
| `trade_id` | STRING | **PK** |
| `market_ticker` | STRING | FK to `market_state_core` |
| `yes_price_dollars` | NUMERIC | Price the YES side traded at |
| `no_price_dollars` | NUMERIC | Price the NO side traded at |
| `count_contracts` | NUMERIC | Number of contracts |
| `taker_side` | STRING | `yes` or `no` |
| `created_time` | TIMESTAMP | Execution time |

**Dedup:** MERGE on `trade_id`. 208K stg rows → 206K core (99% retention).

#### `kpi_5m` — 284 rows

Pre-aggregated 5-minute KPIs. Fast dashboard queries without scanning full tables.

| Column | Type | Notes |
|--------|------|-------|
| `kpi_ts` | TIMESTAMP | 5-minute bucket |
| `series_ticker` | STRING | Per-series breakdown (NULL = all) |
| `open_market_count` | INT64 | Open/active markets |
| `total_volume_dollars` | NUMERIC | Aggregate volume |
| `total_open_interest_dollars` | NUMERIC | Aggregate OI |
| `trade_count_lookback` | INT64 | Trades in lookback window |

#### `dashboard_events` — 22,975 rows, 2.4 MB

User interaction tracking for the dashboard UI.

| Column | Type | Notes |
|--------|------|-------|
| `user_id` | STRING | Who |
| `dashboard_id` | STRING | Which dashboard |
| `action` | STRING | `view`, `filter`, `export` |
| `panel_id` | STRING | Which tile/widget |
| `filter_json` | JSON | Applied filters |

### 2.5 Dashboard views — `kalshi_dash` (18 views)

Read-only views over core tables for UI tiles. No stored data.

| View | Purpose | Time window |
|------|---------|-------------|
| **Operational** | | |
| `vw_pipeline_heartbeat` | Pipeline health (latest trade age, quality failures) | Real-time |
| `vw_quality_checks_24h` | Quality gate pass/fail results | 24h |
| `vw_ingestion_throughput_24h` | API call volume by minute | 24h |
| `vw_endpoint_gap_24h` | API call cadence monitoring | 24h |
| **Market analytics** | | |
| `vw_kpi_5m_24h` | 5-minute KPI time series | 24h |
| `vw_top_active_markets_24h` | Most active markets by volume | 24h |
| `vw_trade_flow_6h` | Trade flow by series and taker side | 6h |
| `vw_most_traded_markets_60m` | Most traded markets | 60m |
| `vw_price_movers_60m` | Biggest price changes | 60m |
| `vw_event_activity_24h` | Event-level aggregates | 24h |
| `vw_orderbook_top_snapshot` | Top-of-book bid/ask levels | Latest |
| **Consumer-facing** | | |
| `vw_simple_side_summary_24h` | Side-level view with favorite/underdog labels | 24h |
| `vw_simple_side_flow_24h` | Side-level trade flow analysis | 24h |
| `vw_simple_side_price_trend_24h` | Hourly price trends by side | 24h |
| `vw_consumer_matchup_view` | Matchup display with resolved winners | 24h |
| **Data science** | | |
| `vw_ds_feature_drift_24h` | Feature drift detection | 24h |
| `vw_ds_label_coverage_24h` | Label coverage metrics | 24h |
| `vw_ds_retrain_signal_24h` | Model retrain priority scoring | 24h |

### 2.6 Signal views — `kalshi_signal` (10 views)

Anomaly detection views that feed into `vw_signal_feed_latest`.

| View | Signal type | Window | Trigger threshold |
|------|-------------|--------|-------------------|
| `vw_signal_probability_shifts_24h` | Probability shift | 24h | >= 8% YES price change |
| `vw_signal_volume_spikes_6h` | Volume spike | 6h | >= 3x baseline volume |
| `vw_signal_volatility_spikes_1h` | Volatility spike | 1h | >= 2x baseline std dev |
| `vw_signal_liquidity_deterioration_latest` | Liquidity deterioration | Latest | >= 15% bid-ask spread |
| `vw_signal_open_interest_change_12h` | OI change | 12h | OI delta by market |
| `vw_signal_cross_market_inconsistencies` | Cross-market inconsistency | Latest | SUM(YES prices) > 1.0 for exclusive outcomes |
| `vw_signal_event_reactions_3h` | Event reaction | 3h | Trade surge after status change |

Helper views:
- `vw_market_latest` — latest snapshot per market (`is_latest = TRUE`)
- `vw_event_latest` — latest event per ticker (`is_latest = TRUE`)

Unified feed: `vw_signal_feed_latest` — UNION ALL of all signal views with a
common schema (`signal_id`, `signal_type`, `severity`, `score`, `explanation_short`, etc.).

### 2.7 Operations layer — `kalshi_ops` (11 tables)

Governance, audit, and metadata.

| Table | Rows | Purpose |
|-------|-----:|---------|
| `rate_limit_log` | 537 | Rate-limit decisions per pipeline run |
| `quality_results` | 2,193 | Quality gate pass/fail per check per run |
| `agent_proposals` | 1,776 | Autonomous pipeline proposals |
| `schema_change_proposals` | 11 | Schema evolution tracking |
| `schema_change_decisions` | — | Proposal approval/rejection |
| `signal_runs` | — | Signal generation tracking |
| `market_intelligence_reports` | — | Periodic report snapshots |
| `dashboard_spec_versions` | — | Dashboard spec version history |

### 2.8 Odds API datasets — `odds_raw`, `odds_stg`, `odds_core`, `odds_ops`

An Airflow DAG (`odds_api_autonomous_de_v0.py`) exists for ingesting data from
The Odds API (traditional sportsbook odds). It has a credit-aware governance
system and fetches NBA odds and scores.

**Current status: all four datasets are empty (0 rows).** The DAG has not been
activated yet.

When active, it would provide:
- `odds_raw`: API telemetry, raw event/score blobs
- `odds_stg`: Parsed odds prices, scores
- `odds_core`: Deduped `odds_prices_core`, `event_outcomes_core`, `kpi_hourly`
- `odds_ops`: Credit budget log, quality results

---

## 3. Known Data Gaps & Issues

### 3.1 NBA market snapshot gap (HIGH impact)

**Problem:** We have **194 NBA events** in `events_dim` but only **13 market
snapshots** in `market_state_core` for NBA. Meanwhile, `trade_prints_core` has
**14,189 NBA trades**.

| Layer | NBA rows |
|-------|---------|
| `events_dim` (is_latest) | 194 |
| `market_state_core` | 13 |
| `trade_prints_core` | 14,189 |

**Root cause:** The pipeline's `/markets` endpoint call filters by `status=open`.
NBA game markets may have short windows where they're "open", and the pipeline
misses them between 5-minute runs. Event metadata is fetched separately via
backfill, so it's captured regardless.

**Impact:** We have rich trade data for NBA but almost no price/probability
snapshots. Can't track how prices move over time for NBA games.

### 3.2 No historical price time-series for NBA (HIGH impact)

**Problem:** The few NBA market snapshots that exist have at most 1-3 data
points, all captured within seconds of each other in a single pipeline run.
There is no multi-hour or multi-day price history for any NBA game contract.

**What it would look like if working:**

```
snapshot_ts              yes_bid_dollars   (Dallas to win)
2026-03-06 10:00:00      0.35             ← market opens
2026-03-06 14:00:00      0.38             ← injury report favors Dallas
2026-03-06 19:30:00      0.28             ← pre-game line movement
2026-03-06 20:15:00      0.45             ← Dallas leads at halftime
2026-03-06 21:30:00      0.92             ← Dallas up 15 in 4th quarter
2026-03-06 22:00:00      1.00             ← settles: Dallas won
```

**What we actually have:** 0-1 snapshots per NBA game market.

### 3.3 `series_ticker` is NULL everywhere (HIGH impact)

**Problem:** All **309,000 rows** in `market_state_core` have `series_ticker = NULL`.

**Root cause:** Likely a parsing issue in the staging layer — the `series_ticker`
field isn't being extracted from the API payload during the JSON → typed-column
transformation.

**Impact:**
- KPI views that group by `series_ticker` return a single NULL bucket
- Signal views that rely on series-level aggregation are degraded
- Can't easily filter markets by category (NBA, weather, politics, etc.)

### 3.4 Odds API not active (MEDIUM impact)

**Problem:** All four `odds_*` datasets are empty. The DAG exists and has
credit-aware governance logic, but hasn't been turned on.

**Impact:** No traditional sportsbook odds data to compare against Kalshi
prices. Cross-platform analysis (Kalshi implied probability vs sportsbook odds)
is not possible yet.

### 3.5 Orderbook sparsity (LOW impact)

**Problem:** Only **2,062 orderbook level rows** and **13,150 raw orderbook
snapshots** vs 309,000 market snapshots. The pipeline fetches orderbooks for the
top 25 markets by volume, but this covers a small fraction of the market
universe.

**Impact:** Limited ability to analyze market depth, liquidity, or order flow
for most markets.

### 3.6 Event endpoint failures (LOW impact)

**Problem:** Many `/events/{ticker}` API calls return 404s when the event has
expired or been delisted.

**Impact:** Some events in `events_dim` may have stale metadata. The pipeline's
backfill strategy prioritizes high-volume markets, so the most important events
are generally covered.

---

## Appendix: Quick Reference Queries

### All contracts for a specific game

```sql
SELECT e.series_ticker, e.title, m.market_ticker,
       m.yes_bid_dollars, m.yes_ask_dollars, m.last_price_dollars
FROM kalshi_core.events_dim e
LEFT JOIN kalshi_core.market_state_core m
  ON m.event_ticker = e.event_ticker AND m.is_latest = TRUE
WHERE e.is_latest = TRUE
  AND e.event_ticker LIKE '%DALBOS%'
ORDER BY e.series_ticker, m.market_ticker
```

### Price history for a market

```sql
SELECT snapshot_ts, yes_bid_dollars, yes_ask_dollars,
       last_price_dollars, volume_dollars
FROM kalshi_core.market_state_core
WHERE market_ticker = '<MARKET_TICKER>'
ORDER BY snapshot_ts
```

### Trade activity for a game

```sql
SELECT t.trade_id, t.created_time, t.yes_price_dollars,
       t.taker_side, t.count_contracts
FROM kalshi_core.trade_prints_core t
WHERE t.market_ticker LIKE '%DALBOS%'
ORDER BY t.created_time
```

### Active signals

```sql
SELECT signal_type, severity, entity_id, title,
       score, explanation_short, signal_ts
FROM kalshi_signal.vw_signal_feed_latest
ORDER BY signal_ts DESC
LIMIT 20
```

### NBA events by series

```sql
SELECT series_ticker,
       COUNT(DISTINCT event_ticker) AS events,
       ARRAY_AGG(DISTINCT title LIMIT 2) AS sample_titles
FROM kalshi_core.events_dim
WHERE is_latest = TRUE AND series_ticker LIKE 'KXNBA%'
GROUP BY series_ticker
ORDER BY events DESC
```
