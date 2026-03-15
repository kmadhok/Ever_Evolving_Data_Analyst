# Governance Operator Runbook

## Purpose

This runbook explains how to operate the dashboard-spec governance loop end to end.

## Prerequisites

- backend running locally or deployed
- UI running locally or deployed
- BigQuery ops tables applied
- service account has write access to `kalshi_ops`

## Operator Workflow

### 1. Validate Environment

```bash
make env-check
make api-test-live DASHBOARD_ID=kalshi_autonomous_v1
```

Expected outputs:
- env check prints config and credentials state
- live validation stores a row in `kalshi_ops.live_validation_runs`

### 2. Open The Operator Console

Navigate to:

```text
http://localhost:5173/ops
```

What you should see:
- active version
- previous version
- proposal backlog
- applied, failed, rejected, rolled back counts
- last run timestamp
- last successful live validation timestamp
- API base and spec source

### 3. Review Proposals

Governed proposals display:
- title
- details
- status
- risk level
- policy version
- decision reason when available

Statuses:
- `proposed`
- `decided`
- `applied`
- `rejected`
- `failed`
- `rolled_back`

### 4. Run The Autonomy Cycle

From the UI:
- click `Run Cycle`

From CLI:

```bash
cd apps/api
./.venv/bin/python -m app.cli governance-run --dashboard-id kalshi_autonomous_v1
```

Expected outputs:
- UI summary timestamps advance
- CLI prints `AutonomyRunResponse`
- `kalshi_ops.autonomy_runs` receives a new row

### 5. Approve Or Reject

For `proposed` items:
- click `Approve`
- or click `Reject`

Expected outputs:
- proposal state changes
- `kalshi_ops.agent_decisions` receives a decision row

### 6. Apply A Decided Proposal

For `decided` items:
- click `Apply`

Expected outputs:
- proposal becomes `applied`
- a new active spec version is inserted
- prior active spec becomes inactive

### 7. Roll Back

For `applied` items:
- click `Rollback`

Expected outputs:
- previous inactive version is restored as active
- proposal becomes `rolled_back`

## Failure Modes

- `Candidate spec failed validation`
  - Meaning: the proposed spec references missing views/columns or violates policy bounds.
  - Action: inspect validation issues in the API response/logs and fix the BigQuery or spec dependency first.

- `Only decided proposals can be applied`
  - Meaning: proposal status transition is out of order.
  - Action: record a governance decision first.

- live validation missing in summary
  - Meaning: `validate-live` has not been run successfully yet.
  - Action: run `make api-test-live`.

## Logs And Results

- run metadata: `kalshi_ops.autonomy_runs`
- live validation metadata: `kalshi_ops.live_validation_runs`
- proposal history: `kalshi_ops.agent_proposals`
- decision history: `kalshi_ops.agent_decisions`
- spec lineage: `kalshi_ops.dashboard_spec_versions`
