# Autonomous Odds Analytics - v0 PRD (Free Tier Optimized)

## Goal
Build a minimal autonomous analytics loop on top of The Odds API v4 free plan (500 credits/month) that:
1. Ingests changing odds/scores into BigQuery without exhausting credits.
2. Produces certified KPI tables for a dashboard.
3. Uses user feedback to adapt only low-risk presentation behavior.

## Scope (v0)
- Sport scope: one sport (`TARGET_SPORT`, default `basketball_nba`).
- Region scope: one region (`TARGET_REGIONS`, default `us`).
- Market scope: one market (`TARGET_MARKETS`, default `h2h`).
- Cadence:
  - Hourly DAG schedule.
  - Free quota heartbeat each run via `/v4/sports/`.
  - Paid endpoint calls only in budget-safe UTC slots.
  - Feedback adaptation daily.

## Free Tier Cost Model
- Monthly cap: 500 credits.
- Odds call estimated cost: `markets x regions` (v0 default = `1 x 1 = 1`).
- Scores call cost: `2`.
- Default full paid cycle estimate: `3` credits.
- Historical endpoints excluded from v0 (high cost).

## Credit Governance Requirements
1. **Budget-aware execution**
- Calculate paid call windows daily from remaining credits and month day.
- Keep a reserve buffer (`CREDIT_RESERVE`) to avoid hard depletion.
- Skip paid calls outside budget windows.

2. **Graceful degradation**
- When credits are tight, downgrade from `odds+scores` to `odds-only`.
- Stop all paid calls when remaining credits fall below reserve + minimum cycle cost.

3. **Usage observability**
- Persist quota headers (`x-requests-remaining`, `x-requests-used`, `x-requests-last`) on every API response.
- Log each budget decision, including why a run was skipped.

## Product Requirements
1. **Ingestion reliability**
- Idempotent loads with ingestion watermarking.
- No-op when budget policy blocks paid calls.
- Retry transient API failures and capture status code.

2. **Data contracts**
- Append-only raw tables.
- Typed staging and deduped core tables.
- Freshness and duplication quality checks before publish.

3. **Autonomous Data Engineer lane (bounded)**
- Agent may propose additive schema/mapping changes.
- Agent may not auto-apply destructive or core-breaking changes.

4. **Dashboard + feedback loop**
- Dashboard reads only `odds_core` tables.
- Log interactions in `dashboard_events`.
- Adapt only low-risk settings (tile order, default filters, alert thresholds).

## Success Metrics
- Credits consumed within plan limit all month.
- Pipeline quality pass rate >= 98%.
- Zero unauthorized core schema breaks.
- Improved dashboard time-to-insight after feedback adaptation.

## Architecture
1. Airflow DAG with:
- free quota heartbeat task,
- credit planning + branch gate,
- paid ingestion branch,
- quality + publish + KPI branch.
2. BigQuery datasets:
- `odds_raw`, `odds_stg`, `odds_core`, `odds_ops`.
3. Optional DE agent proposals in `odds_ops.schema_change_proposals`.

## Phased Rollout
1. **Phase 1**: Static schema + budget-gated ingestion + quality gates.
2. **Phase 2**: DE agent proposals logged only.
3. **Phase 3**: Auto-apply low-risk additive staging changes.
4. **Phase 4**: Controlled promotion path to core.
