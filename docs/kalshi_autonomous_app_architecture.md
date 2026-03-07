# Kalshi Autonomous App Architecture (FastAPI + React)

## Runtime flow

1. Airflow writes market/trade/orderbook data into BigQuery (`raw` -> `stg` -> `core` -> `dash` views).
2. Event entities are ingested via Kalshi `/events/{event_ticker}` and materialized in `kalshi_core.events_dim`.
3. A backfill lane hydrates missing/ticker-like event titles from recent high-volume trade activity.
2. React requests role-filtered dashboard spec from FastAPI (`/v1/dashboard/spec?role=de|analyst|ds`).
3. FastAPI serves latest active spec from `kalshi_ops.dashboard_spec_versions` (fallback default in code).
4. React requests tile data by tile id (`/v1/dashboard/tile/{tile_id}`).
5. FastAPI resolves tile -> BigQuery view and returns rows.
6. React logs usage events (`/v1/usage/events`) into `kalshi_core.dashboard_events`.
7. Agent endpoint (`/v1/agent/proposals`) inspects quality + usage and writes proposals to `kalshi_ops.agent_proposals`.

## Autonomy boundary

- Auto-allowed:
  - UI spec changes (tile order, defaults, adding existing approved views).
  - Proposal generation/persistence.
- Gated:
  - New SQL view/table definitions.
  - Risky metric definitions.
  - Any write path outside dashboard/ops tables.

## Role routes

- `/de`: pipeline reliability, freshness, quality, ingestion cadence.
- `/analyst`: market activity, flow, and short-horizon movers.
- `/ds`: feature drift, label coverage, retrain-priority signals.

## Core API endpoints

- `GET /v1/dashboard/spec?role=de|analyst|ds`
- `POST /v1/dashboard/spec`
- `GET /v1/dashboard/tile/{tile_id}`
- `POST /v1/usage/events`
- `GET /v1/agent/proposals`

## BigQuery app tables

- `kalshi_ops.dashboard_spec_versions`
- `kalshi_ops.agent_proposals`
- `kalshi_ops.agent_decisions`
- `kalshi_core.dashboard_events` (already used for usage telemetry)

## Human-readable context model

- `kalshi_core.events_dim` enriches ticker-heavy market data with event `title` and `sub_title`.
- Analyst and DS views join this table so dashboards communicate business meaning instead of only ticker codes.
