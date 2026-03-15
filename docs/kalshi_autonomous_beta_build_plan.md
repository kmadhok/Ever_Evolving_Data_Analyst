# Kalshi Autonomous Beta Build Plan

## Purpose

This document turns the current v0 scaffold into a concrete build sequence for an autonomous beta.

Phase-level execution detail:

- Phase 1 ticket breakdown: `docs/kalshi_autonomous_beta_phase1_tickets.md`
- Phase 1 issue backlog: `docs/kalshi_autonomous_beta_phase1_issues.md`
- Phase 2 ticket breakdown: `docs/kalshi_autonomous_beta_phase2_tickets.md`

Target outcome:

- The system can autonomously make low-risk dashboard-spec changes.
- Every autonomous action is validated, audited, observable, and reversible.
- The system does not autonomously change core schemas, metrics, or ingestion logic in beta.

## Beta Definition

The repo qualifies as `autonomous beta` when all of the following are true:

1. Proposals are generated on a schedule from production telemetry and pipeline health.
2. Low-risk proposals can be decided automatically under explicit policy.
3. Approved low-risk proposals can activate a new dashboard spec without manual intervention.
4. Invalid specs are rejected before activation.
5. Every apply can be rolled back to the previous active spec.
6. Operators can see proposal, decision, apply, and rollback history.
7. Alerts fire for autonomy failures, stale data, and repeated rollback events.
8. The full loop is covered by unit, integration, and end-to-end tests.

## Current State Summary

What already exists:

- Role-based UI routes for `consumer`, `de`, `analyst`, and `ds`.
- FastAPI endpoints for dashboard spec, tile data, usage events, signal feed, and agent proposals.
- BigQuery tables for `dashboard_spec_versions`, `agent_proposals`, and `agent_decisions`.
- Airflow DAG structure for Kalshi ingestion and quality gates.
- Signal and dashboard views in BigQuery SQL.

What is still missing:

- Decision engine for proposals.
- Automatic spec activation path.
- Rollback flow.
- Spec validation against view existence and role coverage.
- Operator-facing autonomy history.
- Real alerting destination.
- Automated tests for the full autonomy loop.

## Scope

### In Scope For Beta

- Dashboard spec evolution only.
- Low-risk changes:
  - tile order
  - tile visibility by role
  - default row limits within safe caps
  - refresh interval within safe caps
  - adding existing approved views already present in BigQuery
- Proposal, decision, apply, rollback, and audit flows
- Operator UI for reviewing autonomy state
- Telemetry and alerting for autonomy outcomes

### Out Of Scope For Beta

- Autonomous core-schema changes
- Autonomous staging-schema DDL execution
- Autonomous metric-definition changes
- LLM-driven code changes
- Automatic edits to Airflow DAG logic

## Guiding Constraints

1. No destructive writes outside `kalshi_ops` dashboard-governance tables.
2. No spec activation without validation.
3. No auto-apply for medium-risk or high-risk proposals.
4. One active spec per `dashboard_id`.
5. Every autonomous decision must be reproducible from stored inputs and policy version.
6. Rollback must be possible without rebuilding state from logs.

## Build Order

### Phase 0: Baseline Hardening

Goal:

- Make the current scaffold measurable and safe enough to extend.

Deliverables:

- Confirm all role pages load from live BigQuery-backed views.
- Add environment checklist for local, staging, and prod-like runs.
- Add basic test harnesses for API and UI.
- Add canonical sample dashboard spec fixture for tests.

Primary files:

- `apps/api/app`
- `apps/ui/src`
- `docs`
- `README.md`

Exit criteria:

- API starts cleanly in staging.
- UI builds successfully and loads all role routes.
- Each role route has at least one non-empty tile in staging.
- Spec fixture exists and is reused by tests.

### Phase 1: Governance Data Model And Policy

Goal:

- Formalize the state machine for proposals, decisions, applies, and rollbacks.

Deliverables:

- Extend `kalshi_ops.agent_proposals` usage semantics with status lifecycle:
  - `proposed`
  - `decided`
  - `applied`
  - `rejected`
  - `failed`
  - `rolled_back`
- Define decision outcomes:
  - `approve_auto`
  - `approve_manual`
  - `reject`
  - `needs_review`
- Add `policy_version` and `risk_level` to stored proposal payloads.
- Define allowed mutation set for spec diffs.
- Define hard limits:
  - max 1 auto-apply per dashboard per hour
  - max 3 auto-applies per dashboard per day
  - refresh interval floor 30 seconds
  - default tile row cap cannot exceed API safe cap

Primary files:

- `sql/bigquery/006_kalshi_autonomy_app_tables.sql`
- `apps/api/app/models.py`
- `apps/api/app/repository.py`
- `docs/kalshi_autonomous_app_architecture.md`

Exit criteria:

- Proposal and decision payloads have a documented schema.
- Status transitions are explicit and enforced by API logic.
- Policy version is persisted with every decision.

### Phase 2: Spec Validation And Safe Activation

Goal:

- Make it safe to activate specs automatically.

Deliverables:

- Add spec validation module in API.
- Validate:
  - unique `tile_id`
  - valid roles
  - valid datasets and identifiers
  - allowed views only for auto-apply
  - all referenced views exist in BigQuery
  - all referenced columns exist or wildcard is explicitly allowed
  - each role retains at least one visible tile
  - refresh interval within allowed range
- Add spec diff calculator:
  - detect reordered tiles
  - detect visibility changes
  - detect added approved views
  - reject forbidden mutations
- Add activation flow:
  - validate candidate
  - deactivate prior active version
  - insert new active version
  - record apply result
- Add rollback flow:
  - restore previous active version
  - record rollback reason and source proposal

Primary files:

- `apps/api/app/repository.py`
- `apps/api/app/main.py`
- `apps/api/app/models.py`
- new `apps/api/app/spec_validation.py`
- new `apps/api/app/policy.py`

Exit criteria:

- Invalid spec cannot become active.
- API can activate a valid candidate spec automatically.
- API can roll back to previous active spec in one operation.

### Phase 3: Scheduled Proposal Generation And Decisioning

Goal:

- Move proposal generation out of UI-triggered behavior into an actual autonomous control loop.

Deliverables:

- Add a scheduled job to generate proposals from:
  - pipeline heartbeat
  - usage telemetry
  - stale-data signals
  - empty-tile regressions
- Add decision engine:
  - classifies risk
  - applies policy
  - auto-approves low-risk proposals only
  - marks all other proposals as `needs_review`
- Add idempotency keys so the same condition does not spam duplicate proposals.
- Persist the full diff and rationale, not just prose.

Preferred implementation:

- Add an Airflow task lane in `kalshi_market_data_autonomous_de_v0.py` after quality checks and before summary emission.

Primary files:

- `airflow/dags/kalshi_market_data_autonomous_de_v0.py`
- `apps/api/app/repository.py`
- new `apps/api/app/autonomy_service.py`

Exit criteria:

- Proposal generation runs on a schedule without UI traffic.
- Duplicate proposal suppression works across repeated runs.
- Low-risk proposals can move from `proposed` to `decided` automatically.

### Phase 4: Auto-Apply And Rollback Automation

Goal:

- Close the loop from decision to applied behavior.

Deliverables:

- Add apply worker or Airflow task that:
  - fetches auto-approved proposals
  - materializes candidate spec
  - validates candidate spec
  - activates candidate spec
  - stores apply result
- Add post-apply verification:
  - spec fetch succeeds
  - all role-filtered specs remain non-empty
  - critical tiles return data within timeout
- Add rollback triggers:
  - spec validation failure
  - apply error
  - post-apply verification failure
  - repeated operator rollback threshold

Primary files:

- `airflow/dags/kalshi_market_data_autonomous_de_v0.py`
- `apps/api/app/repository.py`
- `apps/api/app/main.py`
- new `apps/api/app/apply_service.py`

Exit criteria:

- One low-risk proposal can be auto-applied end-to-end.
- Failed apply automatically restores previous active spec.
- Rollback events are visible in `kalshi_ops` records.

### Phase 5: Operator And Audit UI

Goal:

- Make autonomous behavior inspectable by humans.

Deliverables:

- Add `/ops` route or equivalent operator panel.
- Show:
  - active spec version
  - last apply time
  - last rollback time
  - proposal queue
  - decision status
  - apply outcome
  - rollback reasons
  - policy version
- Add visible badges in existing role pages:
  - `spec source`
  - `last updated`
  - `autonomy status`
  - stale-data banner if freshness threshold breached

Primary files:

- `apps/ui/src/App.jsx`
- `apps/ui/src/api.js`
- `apps/ui/src/styles.css`
- new operator UI components under `apps/ui/src`

Exit criteria:

- Operator can inspect the last 20 proposals and their outcomes.
- Role pages visibly distinguish fallback default spec from active managed spec.
- Stale-data conditions are surfaced to end users.

### Phase 6: Alerting And Observability

Goal:

- Detect silent failures in the autonomy loop.

Deliverables:

- Emit structured logs for:
  - proposal generation
  - decision result
  - spec validation result
  - apply result
  - rollback result
- Add alert sink for:
  - repeated validation failures
  - repeated rollbacks
  - stale pipeline heartbeat
  - empty critical tiles
  - proposal backlog older than threshold
- Add summary metrics tables or views for autonomy operations.

Primary files:

- `airflow/dags/kalshi_market_data_autonomous_de_v0.py`
- `sql/bigquery/010_kalshi_signal_reporting_tables.sql`
- new SQL under `sql/bigquery`

Exit criteria:

- Alerts arrive in the chosen destination.
- Operators can query weekly autonomy success and rollback rates.

### Phase 7: Test And Launch Readiness

Goal:

- Prove the loop works in staging before beta.

Deliverables:

- Unit tests for policy, diffing, validation, and rollback decisions.
- API integration tests for proposal, decision, apply, and rollback endpoints or services.
- UI end-to-end tests covering all 4 role pages plus operator panel.
- Replay test using historical usage events and pipeline-health snapshots.
- Staging runbook and rollback runbook.

Primary files:

- new `apps/api/tests`
- new `apps/ui/tests`
- `README.md`
- `docs`

Exit criteria:

- Staging passes the full test suite.
- Three successful autonomous low-risk applies occur in staging.
- At least one rollback drill succeeds in staging.
- Beta launch checklist is signed off.

## Technical Spec

### Autonomy State Machine

Proposal lifecycle:

1. `proposed`
2. `decided`
3. `applied`
4. `failed`
5. `rolled_back`

Rejected proposals terminate at:

- `rejected`

Required invariants:

- A proposal can be decided once.
- An applied proposal must reference a resulting `version_id`.
- A rolled-back proposal must reference both failed version and restored version.

### Proposal Payload Contract

Store these fields in `proposal_json`:

```json
{
  "proposal_id": "uuid",
  "dashboard_id": "kalshi_autonomous_v1",
  "proposal_type": "layout_priority",
  "risk_level": "low",
  "policy_version": "2026-03-beta-1",
  "source_signals": {
    "quality_failures_last_60m": 2,
    "minutes_since_latest_trade": 18,
    "top_panel_last_24h": "pipeline_heartbeat"
  },
  "spec_diff": {
    "move_tiles": [
      {
        "tile_id": "pipeline_heartbeat",
        "from_index": 3,
        "to_index": 0
      }
    ],
    "update_refresh_seconds": {
      "from": 60,
      "to": 30
    }
  },
  "rationale": "Quality failures detected in the last hour.",
  "created_at": "2026-03-08T00:00:00Z"
}
```

### Decision Payload Contract

Decision record should contain:

- `proposal_id`
- `dashboard_id`
- `decision`
- `decided_by`
- `decision_reason`
- `decided_at`
- `policy_version`
- `candidate_version_id`

Recommended `decided_by` values:

- `autonomy_policy`
- `human_operator`

### Spec Validation Rules

Validation severity:

- `error`: blocks activation
- `warning`: records issue but allows manual review

Blocking rules:

1. Spec JSON parses into `DashboardSpec`.
2. `dashboard_id` matches requested dashboard.
3. Tile ids are unique.
4. Every tile has at least one valid role.
5. No tile references a non-approved dataset during auto-apply.
6. No tile references a missing view.
7. No tile requests more than `max_tile_rows`.
8. Every role in scope still has at least one tile after mutation.
9. Refresh interval is between 30 and 300 seconds for auto-apply.
10. Auto-apply diff contains only allowed mutation types.

### Allowed Auto-Apply Mutations

- reorder existing tiles
- toggle existing tile visibility by role
- adjust tile `default_limit` down, or up within safe cap
- adjust `refresh_seconds` within safe cap
- add an already-approved view from allowlist

### Forbidden Auto-Apply Mutations

- remove a role entirely
- introduce unknown datasets
- introduce unknown columns
- delete tiles tied to reliability views for `de`
- change metric semantics
- change SQL view definitions
- change ingestion behavior

### Apply Verification Checks

After activation, run:

1. `GET /v1/dashboard/spec` returns the new active spec.
2. `GET /v1/dashboard/spec?role=de|analyst|ds|consumer` each returns at least one tile.
3. Critical tiles load successfully:
  - `pipeline_heartbeat`
  - one analyst tile
  - one ds tile
  - `consumer_matchup_view`
4. Freshness of `pipeline_heartbeat` remains within threshold.

### Rollback Policy

Rollback immediately when:

- spec activation errors
- post-apply verification fails
- critical tile returns repeated empty result or error
- more than one apply failure occurs for the same dashboard within 60 minutes

## Stories And Acceptance Criteria

### Epic 1: Governance And Policy

#### Story 1.1: Define proposal and decision schemas

As an autonomy operator, I want proposal and decision records to have stable schemas so audit and automation logic can rely on them.

Acceptance criteria:

- `proposal_json` includes `risk_level`, `policy_version`, `source_signals`, and `spec_diff`.
- Decision records store `policy_version` and `candidate_version_id`.
- Repository methods reject writes missing required governance fields.

#### Story 1.2: Enforce status transitions

As a backend maintainer, I want proposal statuses to move through explicit states so invalid transitions are impossible.

Acceptance criteria:

- A proposal cannot move from `applied` back to `proposed`.
- A proposal cannot be `applied` without a prior approval decision.
- Invalid transitions return a clear error and are logged.

### Epic 2: Validation And Activation

#### Story 2.1: Validate candidate specs before activation

As the autonomy engine, I want to block invalid candidate specs so the UI never activates a broken layout.

Acceptance criteria:

- Missing views fail validation.
- Duplicate tile ids fail validation.
- Zero-tile role outcomes fail validation.
- Invalid identifiers fail validation.
- Validation returns machine-readable errors.

#### Story 2.2: Activate a valid candidate spec

As the autonomy engine, I want a valid candidate spec to become the new active spec so approved changes take effect automatically.

Acceptance criteria:

- Previous active spec becomes `inactive`.
- New version becomes `active`.
- Returned response includes new `version_id`.
- Activation is idempotent for repeated apply attempts using the same proposal id.

#### Story 2.3: Roll back to previous active spec

As an operator, I want a one-step rollback path so failed autonomous changes can be undone immediately.

Acceptance criteria:

- Rollback restores the last known good spec.
- Rollback records source proposal id and reason.
- The restored version becomes the only active version.

### Epic 3: Scheduled Proposal Generation

#### Story 3.1: Generate proposals without UI traffic

As the platform, I want proposal generation to run on a schedule so autonomy does not depend on a user opening the dashboard.

Acceptance criteria:

- A scheduled job writes proposals at least once per evaluation window.
- UI traffic is not required for proposal creation.
- Proposal timestamps reflect scheduler execution time.

#### Story 3.2: Suppress duplicates

As an operator, I want repeated identical conditions to create one proposal rather than spam the queue.

Acceptance criteria:

- Identical condition and diff combination reuses or suppresses duplicate proposals within the defined window.
- Duplicate suppression logic is covered by tests.

### Epic 4: Automated Decisioning

#### Story 4.1: Auto-approve low-risk proposals

As the policy engine, I want low-risk proposals to be approved automatically so the system can adapt without operator action.

Acceptance criteria:

- Only allowed mutation types are auto-approved.
- Decision records include policy version and rationale.
- Medium-risk and high-risk proposals are not auto-approved.

#### Story 4.2: Route non-low-risk proposals to review

As an operator, I want medium-risk and high-risk proposals to remain queued for review so the beta stays safe.

Acceptance criteria:

- Non-low-risk proposals are marked `needs_review`.
- UI shows pending review status.
- They are never applied automatically.

### Epic 5: Auto-Apply And Verification

#### Story 5.1: Apply approved low-risk proposals

As the autonomy engine, I want approved proposals to activate a new spec automatically so the dashboard can self-tune.

Acceptance criteria:

- Approved proposal creates or materializes a candidate spec.
- Candidate passes validation before activation.
- Apply result is persisted with version id and timestamp.

#### Story 5.2: Verify the live result after apply

As the platform, I want post-apply checks so broken active specs are detected immediately.

Acceptance criteria:

- Role-filtered spec fetch succeeds for all 4 roles.
- Critical tile fetches succeed within timeout.
- Verification failure triggers rollback automatically.

### Epic 6: Operator Experience

#### Story 6.1: Show autonomy state in the UI

As an operator, I want to inspect proposal and apply history from the app so I do not need to query BigQuery manually.

Acceptance criteria:

- Operator page lists the last 20 proposals with statuses.
- Operator page shows active version id and prior version id.
- Operator page shows the last rollback reason if one exists.

#### Story 6.2: Show user-facing freshness and managed-spec status

As a dashboard user, I want to know whether data is stale and whether I am seeing a fallback spec so I can judge trustworthiness.

Acceptance criteria:

- Role page shows `spec source`.
- Role page shows `last updated`.
- Stale-data banner appears when freshness threshold is breached.

### Epic 7: Observability And Alerting

#### Story 7.1: Alert on autonomy failures

As the platform, I want autonomy failures to alert operators so broken loops do not stay silent.

Acceptance criteria:

- Validation failure threshold triggers an alert.
- Repeated rollback threshold triggers an alert.
- Proposal backlog age threshold triggers an alert.

#### Story 7.2: Track autonomy KPIs

As an operator, I want weekly autonomy metrics so I can judge whether beta is safe enough to continue.

Acceptance criteria:

- Queryable metrics exist for proposal count, auto-approval rate, apply success rate, and rollback rate.
- Operator page or SQL view exposes the last 7 days of autonomy KPIs.

### Epic 8: Testing And Launch

#### Story 8.1: Cover the full autonomy loop with automated tests

As a maintainer, I want automated coverage of the core loop so regressions are caught before staging runs.

Acceptance criteria:

- Unit tests cover validation, diffing, policy, and rollback logic.
- Integration tests cover proposal generation through activation.
- End-to-end tests cover all 4 role views and operator page.

#### Story 8.2: Prove staging readiness

As the release owner, I want a clear beta gate so launch is based on evidence, not optimism.

Acceptance criteria:

- 3 successful low-risk auto-applies in staging.
- 1 successful rollback drill in staging.
- 7 consecutive days without unresolved autonomy alert backlog.
- Human operator sign-off recorded in launch checklist.

## Suggested Ticket Breakdown

1. Add governance schema and repository support.
2. Add spec validation and diff engine.
3. Add activation and rollback services.
4. Move proposal generation into scheduled Airflow lane.
5. Add decision engine and idempotency rules.
6. Add post-apply verification checks.
7. Add operator UI and role-page autonomy indicators.
8. Add alerting sink and autonomy KPI views.
9. Add automated tests and staging runbooks.

## Recommended Implementation Sequence

Do not start with the operator UI. The safe order is:

1. governance schema
2. validation and diff engine
3. activation and rollback
4. scheduled proposals
5. automated decisioning
6. auto-apply verification
7. alerts
8. operator UI
9. test hardening

## Beta Launch Checklist

- Governance schema merged
- Validation engine merged
- Rollback path merged
- Scheduled proposal lane merged
- Auto-apply path merged
- Operator UI merged
- Alerting destination configured
- Unit, integration, and end-to-end tests passing
- Staging evidence captured
- Beta go/no-go review completed
