# Phase B & C Implementation Plan

## Current State (as of 2026-03-20)

### What's Working
- **Airflow** running on local Docker (`docker-compose.airflow.yml`), webserver at `localhost:8080`
- **Kalshi DAG** (`kalshi_market_data_autonomous_de_v0`) scheduled every 5 min, fetching markets/trades/events/orderbooks
- **BigQuery** has data across all 3 layers:
  - Raw: `kalshi_raw` (api_call_log, events_raw, markets_raw, trades_raw, orderbooks_raw) — 440K+ rows
  - Staging: `kalshi_stg` (events_stg, market_snapshots_stg, trades_stg, orderbook_levels_stg) — 388K+ rows
  - Core: `kalshi_core` (events_dim, market_state_core, trade_prints_core, kpi_5m) — 368K+ rows
- **Odds DAG** code exists but needs `ODDS_API_KEY` in `.env.airflow.local` to run
- **GCP Project**: `brainrot-453319`, credentials at `./credentials_brainrot.json`

### Known Issue
- `run_post_publish_quality_checks` fails due to `active_event_title_coverage` at 51.7% (threshold requires higher). Self-heals after more runs as event titles get backfilled. Downstream signal/KPI tasks skip due to this. Core data ingestion is unaffected.

---

## Phase B: Strip Governance Complexity

**Goal**: Remove all governance/autonomy code (Phases 1-7 complexity). Keep the codebase focused on: data ingestion (Airflow) + data serving (simple API) + simple dashboard (React).

### B1: Delete governance-only Python files (8 files)

```
rm apps/api/app/autonomy_service.py
rm apps/api/app/policy.py
rm apps/api/app/spec_validation.py
rm apps/api/app/spec_activation.py
rm apps/api/app/spec_rollback.py
rm apps/api/app/spec_diff.py
rm apps/api/app/live_validation.py
rm apps/api/app/cli.py
```

### B2: Delete governance test files (9 of 10 files)

Keep `test_models.py` (tests core data models). Delete:

```
rm apps/api/tests/test_policy.py
rm apps/api/tests/test_repository_governance.py
rm apps/api/tests/test_spec_validation.py
rm apps/api/tests/test_main_governance.py
rm apps/api/tests/test_spec_diff.py
rm apps/api/tests/test_activation_and_rollback.py
rm apps/api/tests/test_autonomy_service.py
rm apps/api/tests/test_live_validation.py
rm apps/api/tests/test_cli.py
```

### B3: Delete governance docs (8 files)

```
rm docs/kalshi_autonomous_app_architecture.md
rm docs/kalshi_autonomous_beta_build_plan.md
rm docs/kalshi_autonomous_beta_phase1_issues.md
rm docs/kalshi_autonomous_beta_phase1_tickets.md
rm docs/kalshi_autonomous_beta_phase2_tickets.md
rm docs/governance_operator_runbook.md
rm docs/local_and_live_validation.md
rm docs/environment_model.md
```

### B4: Delete governance SQL

```
rm sql/bigquery/006_kalshi_autonomy_app_tables.sql
```

Also remove `006_kalshi_autonomy_app_tables.sql` from the `kalshi-core` group in `scripts/apply_bigquery_sql.py`.

### B5: Simplify `apps/api/app/main.py`

**Remove** all governance imports and endpoints. **Keep only**:

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/health` | Health check |
| GET | `/v1/dashboard/spec` | Fetch role-filtered spec |
| GET | `/v1/dashboard/tile/{tile_id}` | Fetch tile data from BigQuery |
| GET | `/v1/signals/feed` | Fetch signal feed |
| POST | `/v1/usage/events` | Log usage events |

**Remove** these endpoints:
- `POST /v1/dashboard/spec` (write spec)
- `POST /v1/dashboard/spec/validate`
- `GET /v1/agent/proposals`
- `GET /v1/governance/proposals`
- `POST /v1/governance/proposals/{proposal_id}/decision`
- `GET /v1/governance/spec-versions`
- `GET /v1/governance/summary`
- `POST /v1/governance/run`
- `POST /v1/governance/apply`
- `POST /v1/governance/rollback`

**Remove** these imports:
- `autonomy_service` (governance_summary, materialize_candidate_spec, run_autonomy_cycle)
- `policy` (POLICY_VERSION)
- `spec_activation` (activate_candidate_spec)
- `spec_rollback` (rollback_dashboard_spec)
- `spec_validation` (validate_dashboard_spec)
- All governance model imports (ProposalPayload, DecisionPayload, GovernedProposalListResponse, GovernanceDecisionRequest, GovernanceDecisionResponse, GovernanceSummaryResponse, ApplyProposalRequest, ApplyProposalResponse, RollbackRequest, RollbackResponse, AutonomyRunResponse, DashboardSpecValidationRequest, DashboardSpecValidationResponse, DashboardSpecVersionListResponse)

### B6: Simplify `apps/api/app/repository.py`

**Keep** these methods:
- `get_dashboard_spec()` — fetches active spec from BQ
- `fetch_tile_rows()` — queries dashboard tiles
- `fetch_signal_feed()` — queries signal data
- `record_usage_event()` — logs user interactions
- `save_dashboard_spec()` — basic spec write (may still be useful)
- `view_exists()`, `dataset_exists()`, `missing_columns()` — validation helpers

**Remove** all governance methods:
- `generate_agent_proposals()`
- `persist_proposals()`
- `insert_governed_proposal()`
- `get_governed_proposal()`
- `list_governed_proposals()`
- `record_proposal_decision()`
- `transition_proposal_status()`
- `activate_dashboard_spec()`
- `get_active_spec_record()`
- `list_spec_versions()`
- `record_autonomy_run()`
- `record_live_validation_run()`
- Any other governance-specific methods

### B7: Simplify `apps/api/app/models.py`

**Keep** (core data models):
- `TileSpec`, `DashboardSpec`
- `TileDataResponse`, `SignalFeedResponse`
- `DashboardSpecResponse`, `DashboardSpecWriteRequest`, `DashboardSpecWriteResponse`
- `UsageEventRequest`
- `AgentProposal`, `AgentProposalResponse` (keep for now, may feed analyst agent)
- Type literals: `VizType`, `SortDir`, `RoleName`

**Remove** (governance models):
- `ProposalPriority`, `ProposalStatus`, `DecisionName`, `RiskLevel`, `DecidedBy`
- `SourceSignals`, `TileMove`, `RoleVisibilityChange`, `TileLimitChange`, `RefreshSecondsChange`, `SpecDiff`
- `ProposalPayload`, `DecisionPayload`
- `GovernedProposalRecord`, `GovernedProposalListResponse`
- `GovernanceDecisionRequest`, `GovernanceDecisionResponse`
- `ValidationSeverity`, `ValidationIssue`
- `DashboardSpecValidationRequest`, `DashboardSpecValidationResponse`
- `DashboardSpecVersionRecord`, `DashboardSpecVersionListResponse`
- `ApplyProposalRequest`, `ApplyProposalResponse`
- `RollbackRequest`, `RollbackResponse`
- `AutonomyRunRecord`, `AutonomyRunResponse`
- `LiveValidationRunRecord`
- `GovernanceSummaryResponse`

### B8: Simplify Airflow DAGs

#### Kalshi DAG (`kalshi_market_data_autonomous_de_v0.py`)

**Remove** these tasks:
- `propose_schema_changes` (lines ~1013-1041)
- `_branch_policy_gate` / `policy_gate` BranchPythonOperator (lines ~1043-1053)
- `apply_schema_change` (lines ~1055-1072)
- `skip_schema_apply` EmptyOperator (line ~1074)
- `run_dashboard_autonomy` (lines ~1971-2007)
- `_import_autonomy_modules` helper function

**Remove** these env vars:
- `AUTONOMY_ENABLE_AUTO_APPLY`
- `AUTONOMY_DASHBOARD_ID`
- `AUTONOMY_ALERT_WEBHOOK_URL`

**Rewire DAG dependencies** after removing governance tasks:
- Currently: `quality_gate >> proposal >> policy_gate >> skip_schema_apply >> published`
- Simplified: `quality_gate >> published` (quality gate branches to either published or stop_noop)
- Remove `run_dashboard_autonomy` from the signal chain

#### Odds DAG (`odds_api_autonomous_de_v0.py`)

Same pattern — remove `propose_schema_changes`, `policy_branch`, `apply_schema_change` tasks and rewire.

### B9: Simplify UI

#### `apps/ui/src/App.jsx`

**Remove** these components:
- `StatusBadge`, `RiskBadge`, `FreshnessBadge`, `SpecSourceBadge`, `ConfirmButton`
- `OpsPanel` (entire governance operator console)

**Remove** ops from role navigation:
- Remove `ops` from `ROLE_CONFIG` object
- Remove ops-related state variables and effects

**Keep**:
- Role-based dashboard rendering (de, analyst, ds, consumer)
- Tile components (ScorecardTile, TableTile, TimeSeriesTile)
- SignalFeedSection
- Basic topbar with refresh

#### `apps/ui/src/api.js`

**Keep**:
- `fetchSpec(dashboardId, role)`
- `fetchTile(dashboardId, tileId, limit)`
- `fetchSignalFeed(params)`
- `postUsageEvent(event)`

**Remove**:
- `fetchProposals(dashboardId)`
- `fetchGovernanceProposals(dashboardId, limit)`
- `applyProposal(payload)`
- `decideGovernanceProposal(proposalId, payload)`
- `fetchGovernanceSummary(dashboardId)`
- `fetchSpecVersions(dashboardId, limit)`
- `rollbackSpec(payload)`
- `runAutonomyCycle(dashboardId)`
- `fetchLiveValidation(dashboardId)`

#### `apps/ui/src/styles.css`

Remove all governance-specific CSS:
- `.status-badge`, `.badge-*` classes
- `.freshness-badge`, `.freshness-*` classes
- `.spec-source-badge`, `.spec-source-*` classes
- `.ops-badges-row`, `.ops-filter-row`, `.filter-chip*` classes
- `.score-mono`, `.score-warn`, `.score-danger*` classes
- `.proposal-header-row`, `.proposal-badges`, `.proposal-meta`, `.proposal-decision-box` classes
- `.confirm-group`, `.btn-danger`, `.btn-ghost` classes
- `.ops-run-*` classes
- `.row-active`, `.cell-mono`, `.cell-notes` classes

### B10: Update `scripts/apply_bigquery_sql.py`

Remove `006_kalshi_autonomy_app_tables.sql` from the `kalshi-core` group:

```python
SQL_GROUPS = {
    "kalshi-core": [
        "002_kalshi_baseline_tables.sql",
        "004_kalshi_dashboard_views.sql",
        # REMOVED: "006_kalshi_autonomy_app_tables.sql",
        "007_kalshi_ds_views.sql",
        "008_kalshi_event_ingestion_tables.sql",
    ],
    ...
}
```

### B11: Commit

Single commit: `Strip governance/autonomy complexity; keep data pipeline and simple dashboard`

---

## Phase C: Build Data Analyst Agent

**Goal**: Standalone Python CLI that queries BigQuery data using natural language and generates automated reports.

### C1: Create `scripts/analyst_agent.py`

Standalone script with two modes:
1. **Interactive Q&A**: `python scripts/analyst_agent.py ask "What are the top 10 markets by volume today?"`
2. **Report generation**: `python scripts/analyst_agent.py report --type daily`

### C2: Architecture

```
User question (natural language)
    |
    v
Claude API (generates SQL from question + schema context)
    |
    v
BigQuery (executes SQL)
    |
    v
Claude API (formats results into human-readable answer)
    |
    v
Formatted output (terminal / markdown file)
```

### C3: Schema Context

Feed the agent metadata about available tables:

| Dataset | Table | Description |
|---------|-------|-------------|
| `kalshi_core` | `events_dim` | Event metadata (ticker, title, status) |
| `kalshi_core` | `market_state_core` | Market snapshots (prices, volume, OI) |
| `kalshi_core` | `trade_prints_core` | Individual trades (price, quantity, side) |
| `kalshi_core` | `kpi_5m` | 5-minute KPI aggregates |
| `kalshi_stg` | `market_snapshots_stg` | Staging market data |
| `kalshi_stg` | `trades_stg` | Staging trade data |
| `kalshi_signal` | `vw_signal_feed_latest` | Market intelligence signals |

### C4: Dependencies

```
google-cloud-bigquery  # already installed in api venv
anthropic              # Claude API client
tabulate               # terminal table formatting
```

### C5: Report Types

- **Daily summary**: Top markets by volume, active event count, trade activity, notable price movements
- **Signal report**: Top signals by score, trending entities, anomaly flags
- **Market deep-dive**: Detailed analysis of a specific event/market ticker

### C6: Example Usage

```bash
# Interactive Q&A
python scripts/analyst_agent.py ask "What are the most traded markets in the last hour?"
python scripts/analyst_agent.py ask "Which events have the highest open interest?"
python scripts/analyst_agent.py ask "Show me price movement for KXBTC markets"

# Automated reports
python scripts/analyst_agent.py report --type daily
python scripts/analyst_agent.py report --type signals
python scripts/analyst_agent.py report --type market --ticker KXBTC

# Save to file
python scripts/analyst_agent.py report --type daily --output reports/daily_2026-03-20.md
```

### C7: Safety Rails

- Read-only BigQuery access (SELECT only, no DML)
- Query cost guardrails (LIMIT enforcement, bytes scanned cap)
- Schema-aware SQL generation (only reference tables that exist)
- Retry logic for transient BigQuery/API errors

---

## Execution Order

1. **Phase B** first (simplify codebase)
2. **Phase C** second (build agent on clean foundation)
3. Commit after each phase
