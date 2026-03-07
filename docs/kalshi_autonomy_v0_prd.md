# Kalshi Market Intelligence - v0 PRD

## Goal
Use Kalshi as the primary autonomous market-data source with a rate-limit aware Airflow DAG, and keep Odds API as an optional companion source.

## Why Kalshi First
1. No monthly credit budget model like The Odds API free plan.
2. Richer market microstructure data (`markets`, `trades`, `orderbook`).
3. Strong fit for continuously updating analytics loops.

## Source of Truth
- Primary source: Kalshi REST API.
- Optional companion: Odds API for cross-source comparison and calibration.

## Scope (v0)
- Poll cadence: every 5 minutes.
- Pull open markets, recent trades, and selected orderbooks.
- Publish certified snapshots/KPIs to BigQuery.
- Keep autonomous DE changes bounded to additive schema/parser updates.

## Current API Constraints (Kalshi)
1. Rate limits are tier-based, not monthly credits.
2. `GET /markets` and `GET /markets/trades` are paginated via `cursor`.
3. `GET /markets/{ticker}/orderbook` supports a `depth` parameter.
4. As of January 15, 2026, cent-denominated market fields are removed from market/event responses; use dollar/fixed-point fields.

## v0 Data Contract
- Raw layer keeps original JSON payloads for replay and schema drift handling.
- Staging layer extracts key fixed-point fields.
- Core layer stores latest market state and trade prints.
- Ops layer stores rate-limit decisions and quality results.

## Guardrails
1. Rate-limit governance with configurable read RPS and request budget per run.
2. Branch to no-op if rate-limit policy disallows expensive pull shapes.
3. No destructive core schema changes by autonomous DE.
4. Quality checks before core publish.

## Success Metrics
1. Zero rate-limit induced pipeline failures in steady state.
2. 5-minute freshness SLA for open markets.
3. Stable quality pass rate >= 98%.
4. Increased analyst usage of dashboard drilldowns over baseline.

## Optional Dual-Source Mode
- Run Kalshi DAG as primary and existing Odds DAG on lower cadence.
- Build reconciliation KPI table by matching market outcomes to implied probabilities.
