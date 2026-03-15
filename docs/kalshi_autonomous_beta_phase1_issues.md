# Kalshi Autonomous Beta Phase 1 Issue Backlog

## Purpose

This document rewrites the Phase 1 ticket set into issue-ready format for GitHub Issues or Jira.

Suggested labels:

- `autonomy-beta`
- `phase-1`
- `api`
- `bigquery`
- `governance`

## Issue 1

Title:

- `P1-01 Extend Kalshi governance table contract`

Summary:

- Formalize the BigQuery persistence contract for governed proposals and decisions.

Problem:

- The current proposal storage supports UI proposal cards, but it does not persist policy metadata, risk level, or idempotency fields needed for a governed autonomy loop.

Scope:

- Update `sql/bigquery/006_kalshi_autonomy_app_tables.sql`
- Update governance documentation in `docs/kalshi_autonomous_app_architecture.md`

Implementation checklist:

- Document canonical proposal statuses.
- Document canonical decision values.
- Document canonical risk levels.
- Add additive columns for:
  - `agent_proposals.risk_level`
  - `agent_proposals.policy_version`
  - `agent_proposals.idempotency_key`
  - `agent_decisions.policy_version`
  - `agent_decisions.candidate_version_id`
- Clarify that `proposal_json` is the authoritative governed payload.

Acceptance criteria:

- SQL file defines the additive governance fields.
- Documentation states the canonical enums and payload authority.
- Existing proposal persistence remains backward-compatible.

Dependencies:

- none

Estimate:

- 1 engineer day

## Issue 2

Title:

- `P1-02 Add typed governance payload models`

Summary:

- Introduce typed models for governed proposals, decisions, source signals, and spec diffs.

Problem:

- Governance metadata is currently untyped, making future policy, validation, and apply logic brittle.

Scope:

- Update `apps/api/app/models.py`

Implementation checklist:

- Add typed enums or `Literal` values for:
  - proposal status
  - decision value
  - risk level
  - decided_by
- Add models for:
  - `SourceSignals`
  - `SpecDiff`
  - `ProposalPayload`
  - `DecisionPayload`
  - `GovernedProposalRecord`
- Keep existing `AgentProposal` compatible with current UI behavior.

Acceptance criteria:

- Governance payloads validate through Pydantic.
- Invalid enum values fail validation.
- Existing UI proposal response types remain usable.

Dependencies:

- `P1-01 Extend Kalshi governance table contract`

Estimate:

- 1 engineer day

## Issue 3

Title:

- `P1-03 Add repository helpers for governed proposals and transitions`

Summary:

- Centralize governed proposal persistence and status-transition enforcement in the repository layer.

Problem:

- Proposal persistence is currently optimized for lightweight UI cards and does not enforce a governance lifecycle.

Scope:

- Update `apps/api/app/repository.py`

Implementation checklist:

- Add repository helpers for:
  - insert governed proposal
  - read governed proposal by id
  - list recent governed proposals
  - record a proposal decision
  - transition proposal status
- Enforce allowed transitions.
- Persist `policy_version` with proposal and decision records.
- Store `idempotency_key` in governed proposal payloads.
- Preserve compatibility with pre-migration table schemas where possible.

Acceptance criteria:

- Invalid proposal status transitions are rejected.
- Governed proposal records can be queried by `proposal_id`.
- Decisions are stored and transition proposals to `decided`.
- Existing lightweight proposal persistence still works.

Dependencies:

- `P1-01 Extend Kalshi governance table contract`
- `P1-02 Add typed governance payload models`

Estimate:

- 1.5 engineer days

## Issue 4

Title:

- `P1-04 Add deterministic autonomy policy module`

Summary:

- Introduce a canonical policy module to hold policy version and risk helper logic.

Problem:

- Future governance and apply logic need one code-level policy source instead of ad hoc strings.

Scope:

- Add `apps/api/app/policy.py`

Implementation checklist:

- Define `POLICY_VERSION`.
- Add deterministic helpers for mapping current proposal priority into initial risk.
- Keep the module free of BigQuery calls.
- Prepare it for later decision eligibility rules.

Acceptance criteria:

- Policy version exists in one canonical module.
- Repository code imports policy helpers instead of hardcoding policy strings.
- Module is pure business logic.

Dependencies:

- `P1-02 Add typed governance payload models`

Estimate:

- 1 engineer day

## Issue 5

Title:

- `P1-05 Add governance read/decision API endpoints`

Summary:

- Expose an API surface for governed proposal history and decision recording.

Problem:

- There is no operational endpoint for reading governed proposal history or recording decisions.

Scope:

- Update `apps/api/app/main.py`
- Update `apps/api/app/models.py`
- Update `apps/api/app/repository.py`

Implementation checklist:

- Add `GET` endpoint for governed proposal history by dashboard.
- Add `POST` endpoint to record a decision for a proposal.
- Return status, policy version, risk level, and decision summary.
- Keep `/v1/agent/proposals` intact for UI proposal cards.

Acceptance criteria:

- Operator can fetch recent governed proposals through API.
- Operator can record a valid decision through API.
- Invalid proposal ids or invalid transitions return 4xx.

Dependencies:

- `P1-02 Add typed governance payload models`
- `P1-03 Add repository helpers for governed proposals and transitions`
- `P1-04 Add deterministic autonomy policy module`

Estimate:

- 1.5 engineer days

## Issue 6

Title:

- `P1-06 Add governance test coverage`

Summary:

- Add automated tests for the governance contract before further autonomy work proceeds.

Problem:

- The next phases depend on status transitions and policy metadata staying correct under change.

Scope:

- Add tests under `apps/api/tests`

Implementation checklist:

- Add model validation tests.
- Add repository transition tests.
- Add policy tests.
- Add API endpoint tests for governance history and decisions.

Acceptance criteria:

- Invalid transition cases are covered.
- Policy version persistence is asserted.
- Governance tests pass locally and in CI.

Dependencies:

- `P1-02 Add typed governance payload models`
- `P1-03 Add repository helpers for governed proposals and transitions`
- `P1-04 Add deterministic autonomy policy module`
- `P1-05 Add governance read/decision API endpoints`

Estimate:

- 2 engineer days

## Issue 7

Title:

- `P1-07 Update architecture and operator governance docs`

Summary:

- Align architecture docs and onboarding notes with the implemented governance state machine.

Problem:

- Without doc updates, later implementation phases will interpret statuses and payload fields inconsistently.

Scope:

- Update `docs/kalshi_autonomous_app_architecture.md`
- Update `docs/kalshi_autonomous_beta_build_plan.md`
- Update `README.md` if new governance endpoints are added

Implementation checklist:

- Document the governance state machine.
- Document canonical payload fields.
- Link the issue/ticket docs from the broader beta build plan.

Acceptance criteria:

- Docs reflect the implemented governance contract.
- Another engineer can find the lifecycle and fields without reading the repository code first.

Dependencies:

- `P1-05 Add governance read/decision API endpoints`

Estimate:

- 0.5 engineer day
